"""Reranking of fused candidates.

`rerank(conn, query, hits)` takes the fused candidate set from `retrieve()`
and reorders it by true query-document relevance — the signal rank fusion
can't see. Two swappable backends, chosen by env ``MOO_RERANKER``:

- ``llm`` (default) — one cheap listwise LLM call, cached by query+candidate
  set, heuristic-identity fallback when keyless. A response the stage cannot use
  is cached too, so a repeated query never re-pays for the same bad answer.
- ``cross`` — a local cross-encoder (default
  ``cross-encoder/ms-marco-MiniLM-L-6-v2``, override with
  ``MOO_CROSS_ENCODER_MODEL``) scores each (query, text) pair on CPU: no API,
  no key, deterministic, ~10ms/pair. Scores land in
  ``hit.rank_signals["cross"]`` for explainability.
- ``off`` — identity (pure fused order).

Every backend is a refinement stage with the same guarantee: on any failure
the fused order is returned unchanged — reranking can only help, never break.

CLI:  ``uv run python -m app.rerank "why is my query slow"``
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3

from . import llm
from .expand import normalize_query

log = logging.getLogger("moo.rerank")

MAX_CANDIDATES = 20
SNIPPET_CHARS = 350
DEFAULT_CROSS_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_cross_model = None

_RERANK_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {"type": "array", "items": {"type": "integer"}},
        "irrelevant": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["order"],
    "additionalProperties": False,
}

_PROMPT = """Rank the candidate chunks by how well they answer the developer query
about databases. Return `order` = chunk_ids from most to least relevant, and
`irrelevant` = chunk_ids that are off-topic or unhelpful (they will be dropped).
Judge relevance to the query's actual intent, not keyword overlap.

Query: {query}

Candidates:
{candidates}"""


_UNUSABLE = {"order": [], "unusable": True}


def _cache_key(query: str, chunk_ids: list[int]) -> str:
    payload = normalize_query(query) + "|" + ",".join(map(str, sorted(chunk_ids)))
    return llm.cache_key("rerank", payload)


def _llm_order(query: str, candidates: list[tuple[int, str]]) -> dict | None:
    joined = "\n\n".join(f"[chunk {cid}] {text[:SNIPPET_CHARS]}" for cid, text in candidates)
    payload = llm.generate_json(
        _PROMPT.format(query=query, candidates=joined), schema=_RERANK_SCHEMA, max_tokens=400
    )
    if not payload or not isinstance(payload.get("order"), list):
        return None
    return payload


def _apply(hits: list, order: list[int], irrelevant: set[int]) -> list:
    """Reorder hits by `order`, drop `irrelevant`, and append any hit the model
    didn't mention (preserving its RRF position) so nothing is silently lost."""
    by_id = {h.chunk_id: h for h in hits}
    seen: set[int] = set()
    out = []
    for cid in order:
        h = by_id.get(cid)
        if h is not None and cid not in irrelevant and cid not in seen:
            out.append(h)
            seen.add(cid)
    for h in hits:
        if h.chunk_id not in seen and h.chunk_id not in irrelevant:
            out.append(h)
            seen.add(h.chunk_id)
    return out


def _get_cross_model():
    global _cross_model
    if _cross_model is None:
        from sentence_transformers import CrossEncoder

        name = os.environ.get("MOO_CROSS_ENCODER_MODEL", DEFAULT_CROSS_MODEL)
        log.info("loading cross-encoder %s", name)
        _cross_model = CrossEncoder(name, device="cpu")
    return _cross_model


def cross_rerank(query: str, hits: list, *, top_n: int = MAX_CANDIDATES) -> list:
    """Reorder the top-N hits by local cross-encoder relevance; the tail keeps
    its fused order. Failure returns hits unchanged."""
    if len(hits) < 2:
        return hits
    head, tail = hits[:top_n], hits[top_n:]
    try:
        scores = _get_cross_model().predict([(query, h.text) for h in head])
    except Exception as exc:  # noqa: BLE001
        log.warning("cross-encoder rerank failed, keeping fused order: %s", exc)
        return hits
    for h, s in zip(head, scores, strict=True):
        signals = getattr(h, "rank_signals", None)
        if isinstance(signals, dict):
            signals["cross"] = round(float(s), 4)
    order = sorted(range(len(head)), key=lambda i: -float(scores[i]))
    return [head[i] for i in order] + tail


def rerank(
    conn: sqlite3.Connection, query: str, hits: list, *, use_llm: bool = True
) -> list:
    """Return `hits` reordered/filtered by relevance. Backend per MOO_RERANKER
    (llm default | cross | off); identity when trivial or unavailable."""
    if len(hits) < 2:
        return hits
    backend = os.environ.get("MOO_RERANKER", "llm").strip().lower()
    if backend == "off":
        return hits
    if backend == "cross":
        return cross_rerank(query, hits)
    if backend != "llm":
        log.warning("unknown MOO_RERANKER %r; using llm backend", backend)
    candidates = [(h.chunk_id, h.text) for h in hits[:MAX_CANDIDATES]]
    chunk_ids = [cid for cid, _ in candidates]

    key = _cache_key(query, chunk_ids)
    payload = llm.cache_get(conn, key)
    if payload is None and use_llm:
        payload = _llm_order(query, candidates)
        if payload is None and llm.get_client():
            payload = dict(_UNUSABLE)
        if payload is not None:
            llm.cache_put(conn, key, payload, llm.CHEAP_MODEL)
    if payload is None or payload.get("unusable"):
        return hits

    valid = set(chunk_ids)
    order = [c for c in payload.get("order", []) if c in valid]
    irrelevant = {c for c in payload.get("irrelevant", []) if c in valid}
    reranked = _apply(hits, order, irrelevant)
    log.info("reranked %d -> %d (dropped %d)", len(hits), len(reranked), len(hits) - len(reranked))
    return reranked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.rerank", description="Rerank fused candidates")
    parser.add_argument("query")
    parser.add_argument("-k", type=int, default=10)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from .index.vector import connect
    from .retrieve import retrieve

    conn = connect()
    hits = retrieve(conn, args.query, k=args.k)
    reranked = rerank(conn, args.query, hits, use_llm=not args.no_llm)
    for rank, h in enumerate(reranked, 1):
        print(f"  {rank:>2}. #{h.chunk_id:<5} {h.source_type:14} {h.text[:70].strip()!r}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
