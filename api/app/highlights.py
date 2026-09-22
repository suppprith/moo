"""Span highlights + calibrated relevance.

A whole ~450-token chunk is a lot of context for an agent to budget; the
tightest relevant span is usually what it actually needs. For each returned
hit this module extracts the most query-relevant sentence(s) as ``highlights``
and a ``relevance`` score in [0, 1] — the cosine similarity of the best span
to the query, which (unlike raw RRF/fused scores) is comparable **across**
queries: 0.9 means "directly on topic" for every query, not just this one.

One batched encode scores every sentence of every hit in a single model call.
Fenced code blocks are kept whole as candidate spans (the exact code line is
often the answer); tiny fragments are skipped.

A span that says something changed ("deprecated since 3.12", "was removed in
8.0", "renamed to proxy.ts") takes the last highlight slot when it is nearly as
on-topic as the best span. That sentence is usually the one an agent most needs
and least expects, and similarity alone ranks it below the how-to text around it.
"""

from __future__ import annotations

import re

from .embed import embed_texts
from .index.vector import QUERY_PREFIX

_FENCE_RE = re.compile(r"```.*?```", re.S)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

MAX_SENTENCES_PER_TEXT = 12
MIN_SPAN_CHARS = 25
MAX_SPAN_CHARS = 400
DEFAULT_TOP = 2
LIFECYCLE_MARGIN = 0.12

_LIFECYCLE_RE = re.compile(
    r"(deprecat\w*|removed in|was removed|been removed|no longer|superseded|obsolete|"
    r"replaced by|renamed|retired|retirement|end[- ]of[- ]life|legacy)", re.I)


def _spans(text: str) -> list[str]:
    """Candidate spans: prose sentences + whole code fences, document order."""
    out: list[str] = []
    pos = 0
    segments: list[tuple[str, str]] = []
    for m in _FENCE_RE.finditer(text):
        segments.append(("prose", text[pos : m.start()]))
        segments.append(("code", m.group(0)))
        pos = m.end()
    segments.append(("prose", text[pos:]))
    for kind, seg in segments:
        if kind == "code":
            if len(seg) >= MIN_SPAN_CHARS:
                out.append(seg.strip()[:MAX_SPAN_CHARS])
            continue
        for sent in _SENT_SPLIT.split(seg):
            sent = sent.strip()
            if len(sent) >= MIN_SPAN_CHARS:
                out.append(sent[:MAX_SPAN_CHARS])
        if len(out) >= MAX_SENTENCES_PER_TEXT:
            break
    return out[:MAX_SENTENCES_PER_TEXT]


def highlight_hits(query: str, texts: list[str], *, top: int = DEFAULT_TOP) -> list[dict]:
    """For each text: ``{"highlights": [best spans, document order],
    "relevance": float}``. One batched encode for all spans of all texts."""
    per_text_spans = [_spans(t or "") for t in texts]
    flat: list[str] = []
    owner: list[int] = []
    for i, spans in enumerate(per_text_spans):
        for s in spans:
            flat.append(s)
            owner.append(i)

    if not flat:
        return [{"highlights": [], "relevance": 0.0} for _ in texts]

    qv = embed_texts([QUERY_PREFIX + query])[0]
    span_vecs = embed_texts(flat)
    sims = span_vecs @ qv

    results = [{"highlights": [], "relevance": 0.0} for _ in texts]
    by_owner: dict[int, list[tuple[int, float, str]]] = {}
    for j, (i, sim) in enumerate(zip(owner, sims, strict=True)):
        by_owner.setdefault(i, []).append((j, float(sim), flat[j]))
    for i, scored in by_owner.items():
        ranked = sorted(scored, key=lambda t: -t[1])
        best = ranked[:top]
        best = _with_lifecycle_span(best, ranked[top:])
        best.sort(key=lambda t: t[0])
        results[i] = {
            "highlights": [s for _, _, s in best],
            "relevance": round(max(0.0, ranked[0][1]), 4),
        }
    return results


def _with_lifecycle_span(best: list, rest: list) -> list:
    """Swap the weakest highlight for the best span that says something changed,
    if none of the picks already does and that span is within LIFECYCLE_MARGIN of
    the top similarity. The top span always stays."""
    if len(best) < 2 or any(_LIFECYCLE_RE.search(s) for _, _, s in best):
        return best
    floor = best[0][1] - LIFECYCLE_MARGIN
    for cand in rest:
        if cand[1] < floor:
            break
        if _LIFECYCLE_RE.search(cand[2]):
            return best[:-1] + [cand]
    return best
