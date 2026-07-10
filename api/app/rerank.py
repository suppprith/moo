"""Listwise LLM reranking of fused candidates (SUP-90).

`rerank(conn, query, hits)` takes the RRF candidate set from `retrieve()` and
asks a cheap Gemini call to reorder it by true relevance and drop off-topic
chunks — the cross-encoder-style signal RRF can't see. It's a refinement stage:
retrieval still decides the candidate pool, rerank only reorders/filters it.

Guards:
- **Budget** — at most ``MAX_CANDIDATES`` snippets go to the model, and exactly
  one call per query. No candidates over budget are ever sent.
- **Cache** — keyed by query + the candidate chunk-id set, so repeat/demo
  queries skip the LLM entirely (``llm_cache`` stage ``rerank``).
- **Fallback** — no credentials (or a failed/empty call) returns the RRF order
  unchanged, so reranking can only help, never regress.

CLI:  ``uv run python -m app.rerank "why is my query slow"``
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3

from . import llm
from .expand import normalize_query

log = logging.getLogger("moo.rerank")

MAX_CANDIDATES = 20     # budget: never send more than this many snippets
SNIPPET_CHARS = 350

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
    for h in hits:  # tail: unranked survivors keep original order
        if h.chunk_id not in seen and h.chunk_id not in irrelevant:
            out.append(h)
            seen.add(h.chunk_id)
    return out


def rerank(
    conn: sqlite3.Connection, query: str, hits: list, *, use_llm: bool = True
) -> list:
    """Return `hits` reordered/filtered by relevance. Identity (RRF order) when
    no LLM is available or the candidate set is trivial."""
    if len(hits) < 2:
        return hits
    candidates = [(h.chunk_id, h.text) for h in hits[:MAX_CANDIDATES]]
    chunk_ids = [cid for cid, _ in candidates]

    key = _cache_key(query, chunk_ids)
    payload = llm.cache_get(conn, key)
    if payload is None and use_llm:
        payload = _llm_order(query, candidates)
        if payload is not None:
            llm.cache_put(conn, key, payload, llm.CHEAP_MODEL)
    if payload is None:
        return hits  # fallback: unchanged RRF order

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
