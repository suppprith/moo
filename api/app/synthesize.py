"""Answer synthesis with inline citations.

`synthesize(conn, query, claims)` turns the evidence layer's claims into a short
answer where **every sentence cites at least one source** (`[S1]`, `[S2]`…) and
**disputed claims are shown as a disagreement**, never averaged into false
confidence.

Sources are the documents backing the claims (via `evidence` + `claim_chunk`),
deduped and numbered. Primary path is a Gemini call constrained to those sources;
a validation pass drops any sentence with no citation. If the model is
unavailable — or its output validates to nothing — a deterministic template
composes cited sentences directly from the claims, so the answer is grounded by
construction.

CLI:  ``uv run python -m app.synthesize "Postgres vs MySQL for a new app"``
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3

from . import llm

log = logging.getLogger("moo.synthesize")

MAX_CLAIMS = 6
_CITE = re.compile(r"\[S\d+\]")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")

_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

_PROMPT = """Write a short answer (2-4 sentences) to the developer query using ONLY
the claims below. Rules:
- Every sentence must cite at least one source as [S#] (the numbers given).
- For any claim marked DISPUTED, say explicitly that sources disagree and cite
  both the supporting and contradicting side. Never average a disagreement away.
- Do not introduce facts that aren't in the claims. No preamble.

Query: {query}

Claims:
{claims}

Sources you may cite: {source_ids}"""


def _claim_sources(conn: sqlite3.Connection, claim_id: int) -> dict[str, list[int]]:
    """Backing document ids for a claim, split by side. `evidence` edges give
    supports/contradicts/explains; `claim_chunk` provenance counts as support."""
    rows = conn.execute(
        """
        SELECT e.relation, COALESCE(canon.document_id, ch.document_id) AS doc_id
        FROM evidence e
        JOIN chunk ch ON ch.id = e.chunk_id
        LEFT JOIN chunk canon ON canon.id = ch.canonical_chunk_id
        WHERE e.claim_id = ?
        """,
        (claim_id,),
    ).fetchall()
    support, contra = [], []
    for r in rows:
        (contra if r["relation"] == "contradicts" else support).append(r["doc_id"])
    if not support:
        prov = conn.execute(
            """
            SELECT COALESCE(canon.document_id, ch.document_id) AS doc_id
            FROM claim_chunk cc
            JOIN chunk ch ON ch.id = cc.chunk_id
            LEFT JOIN chunk canon ON canon.id = ch.canonical_chunk_id
            WHERE cc.claim_id = ?
            """,
            (claim_id,),
        ).fetchall()
        support = [r["doc_id"] for r in prov]
    return {"supports": support, "contradicts": contra}


class _SourceRegistry:
    """Assigns stable S1..Sn numbers to documents in first-reference order."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._index: dict[int, int] = {}
        self.sources: list[dict] = []

    def ref(self, doc_id: int) -> int:
        if doc_id in self._index:
            return self._index[doc_id]
        row = self.conn.execute(
            "SELECT url, title, source_type, trust_score FROM document WHERE id = ?", (doc_id,)
        ).fetchone()
        idx = len(self.sources) + 1
        self._index[doc_id] = idx
        self.sources.append({
            "index": idx, "document_id": doc_id,
            "url": row["url"] if row else None,
            "title": row["title"] if row else None,
            "source_type": row["source_type"] if row else None,
            "trust_score": row["trust_score"] if row else None,
        })
        return idx

    def cite(self, doc_ids: list[int]) -> list[int]:
        seen, out = set(), []
        for d in doc_ids:
            i = self.ref(d)
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out


def _cited(text: str, indices: list[int]) -> str:
    body = text.rstrip()
    if body and body[-1] not in ".!?":
        body += "."
    return body + " " + "".join(f"[S{i}]" for i in indices)


def _template_answer(prepared: list[dict]) -> str:
    """Deterministic NLG: one cited sentence per claim, disputes made explicit."""
    sentences = []
    for c in prepared:
        if c["disputed"] and c["contradicts"]:
            sup = "".join(f"[S{i}]" for i in c["supports"])
            con = "".join(f"[S{i}]" for i in c["contradicts"])
            sentences.append(
                f"Sources disagree on this: some hold that {c['text'].rstrip('.')} {sup}, "
                f"while others contradict it {con}."
            )
        elif c["supports"]:
            sentences.append(_cited(c["text"], c["supports"]))
    return " ".join(sentences)


def _validate(answer: str, valid_ids: set[int]) -> str:
    """Keep only sentences that carry ≥1 citation to a real source."""
    kept = []
    for sent in _SENT_SPLIT.split(answer.strip()):
        cites = {int(m[2:-1]) for m in _CITE.findall(sent)}
        if cites & valid_ids:
            kept.append(sent.strip())
    return " ".join(kept)


def _llm_answer(
    conn: sqlite3.Connection, query: str, prepared: list[dict], source_ids: list[int]
) -> str | None:
    lines = []
    for i, c in enumerate(prepared, 1):
        tag = "DISPUTED" if c["disputed"] and c["contradicts"] else f"confidence {c['confidence']}"
        cite = "supporting " + ",".join(f"S{s}" for s in c["supports"])
        if c["contradicts"]:
            cite += " ; contradicting " + ",".join(f"S{s}" for s in c["contradicts"])
        lines.append(f'[C{i}] ({tag}) "{c["text"]}" — {cite}')
    key_input = query.strip().lower() + ":" + "|".join(f"{c['text']}>{c['supports']}/{c['contradicts']}" for c in prepared)
    payload = llm.cached_json(
        conn, "synthesize", key_input,
        _PROMPT.format(
            query=query, claims="\n".join(lines),
            source_ids=", ".join(f"S{i}" for i in source_ids),
        ),
        schema=_ANSWER_SCHEMA, max_tokens=500,
    )
    if not payload or not isinstance(payload.get("answer"), str):
        return None
    return payload["answer"]


def synthesize(
    conn: sqlite3.Connection, query: str, claims: list[dict], *, use_llm: bool = True
) -> dict:
    """Compose a cited answer from `claims` (dicts with id/text/confidence/
    disputed). Returns {answer, sources, disputed, generator}."""
    ranked = sorted(
        claims, key=lambda c: (c.get("disputed", False), c.get("confidence") or 0), reverse=True
    )[:MAX_CLAIMS]
    registry = _SourceRegistry(conn)

    prepared = []
    for c in ranked:
        sides = _claim_sources(conn, c["id"])
        supports = registry.cite(sides["supports"])
        contradicts = registry.cite(sides["contradicts"])
        if not supports and not contradicts:
            continue
        prepared.append({
            "text": c["text"], "confidence": c.get("confidence"),
            "disputed": bool(c.get("disputed")), "supports": supports, "contradicts": contradicts,
        })

    if not prepared:
        return {"answer": "", "sources": [], "disputed": [], "generator": "none"}

    valid_ids = {s["index"] for s in registry.sources}
    generator = "template"
    answer = ""
    if use_llm:
        raw = _llm_answer(conn, query, prepared, sorted(valid_ids))
        if raw:
            answer = _validate(raw, valid_ids)
            if answer:
                generator = llm.CHEAP_MODEL
    if not answer:
        answer = _template_answer(prepared)

    disputed = [c["text"] for c in prepared if c["disputed"] and c["contradicts"]]
    return {
        "answer": answer,
        "sources": registry.sources,
        "disputed": disputed,
        "generator": generator,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.synthesize", description="Synthesize a cited answer")
    parser.add_argument("query")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from .evidence.claims import extract_claims
    from .index.vector import connect

    conn = connect()
    claims = extract_claims(conn, args.query, use_llm=not args.no_llm)
    result = synthesize(conn, args.query, claims, use_llm=not args.no_llm)
    print(json.dumps(result, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
