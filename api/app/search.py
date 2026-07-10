"""Unified search pipeline + response contract (SUP-92).

`search(conn, query, mode=...)` ties the whole pipeline together and returns the
single contract the web UI, command palette, and CLI all build against:

    { query, mode, intent, answer, claims[], graph, sources[], citations, meta }

**AI is opt-in via `mode`** (the product's core principle):

- ``raw``    (default) pure retrieval — understanding + heuristic fan-out +
             hybrid retrieval. **Zero LLM calls**, target < 1s.
- ``claims`` adds LLM rerank + claim extraction + evidence linking + confidence,
             plus the query subgraph.
- ``full``   adds a cited, synthesized answer.

Cost and latency scale with mode; ``raw`` never touches a model. Each heavier
stage still degrades to its heuristic fallback when no credentials are present,
so every mode runs end-to-end without a key (lower quality, same shape).

CLI:  ``uv run python -m app.search "Postgres vs MySQL" --mode full``
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time

from .retrieve import retrieve

log = logging.getLogger("moo.search")

MODES = ("raw", "claims", "full")
CONTRACT_VERSION = "1.0"
DEFAULT_K = 10


def _sources(hits: list) -> list[dict]:
    return [
        {
            "chunk_id": h.chunk_id,
            "document_url": h.document_url,
            "title": h.title,
            "source_type": h.source_type,
            "trust_score": h.trust_score,
            "heading": h.heading,
            "url_anchor": h.url_anchor,
            "score": round(h.score, 6),
        }
        for h in hits
    ]


def _claims_payload(conn: sqlite3.Connection, claim_ids: list[int]) -> list[dict]:
    """Reload the given claims with confidence + their evidence edges."""
    if not claim_ids:
        return []
    qmarks = ",".join("?" * len(claim_ids))
    claims = conn.execute(
        f"SELECT id, text, confidence, disputed FROM claim WHERE id IN ({qmarks})", claim_ids
    ).fetchall()
    edges = conn.execute(
        f"""
        SELECT e.claim_id, e.relation, e.chunk_id, e.strength, d.url AS document_url
        FROM evidence e
        JOIN chunk ch ON ch.id = e.chunk_id
        JOIN document d ON d.id = ch.document_id
        WHERE e.claim_id IN ({qmarks})
        """,
        claim_ids,
    ).fetchall()
    by_claim: dict[int, list[dict]] = {}
    for e in edges:
        by_claim.setdefault(e["claim_id"], []).append({
            "relation": e["relation"], "chunk_id": e["chunk_id"],
            "strength": e["strength"], "document_url": e["document_url"],
        })
    out = []
    for c in sorted(claims, key=lambda r: (r["confidence"] is None, -(r["confidence"] or 0))):
        out.append({
            "id": c["id"], "text": c["text"],
            "confidence": c["confidence"], "disputed": bool(c["disputed"]),
            "evidence": by_claim.get(c["id"], []),
        })
    return out


def search(
    conn: sqlite3.Connection, query: str, *, mode: str = "raw", k: int = DEFAULT_K,
    use_llm: bool = True,
) -> dict:
    """Run the pipeline to the depth `mode` requests and return the unified
    contract. `use_llm=False` forces every stage onto its heuristic path."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    started = time.perf_counter()

    from .understand import understand

    u = understand(query, conn)

    # ---- retrieval (every mode) --------------------------------------------
    if mode == "raw":
        # pure retrieval: heuristic fan-out only, guaranteed zero LLM calls
        from .expand import heuristic_expand

        variants = heuristic_expand(query)
    else:
        from .expand import expand

        variants = expand(conn, query, use_llm=use_llm)
    hits = retrieve(conn, query, k=k, queries=variants, source_boost=u.source_boost)

    response: dict = {
        "query": query,
        "mode": mode,
        "intent": u.intent,
        "answer": None,
        "claims": [],
        "graph": {"nodes": [], "edges": []},
        "sources": _sources(hits),
        "citations": [],
        "meta": {"contract_version": CONTRACT_VERSION, "entities": u.entities},
    }

    if mode == "raw":
        response["meta"]["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return response

    # ---- evidence layer (claims + full) ------------------------------------
    from .evidence.claims import extract_claims
    from .evidence.confidence import score_claim
    from .evidence.links import link_claim
    from .graph.query import graph_for_query
    from .rerank import rerank

    hits = rerank(conn, query, hits, use_llm=use_llm)
    response["sources"] = _sources(hits)

    claims = extract_claims(conn, query, k=k, use_llm=use_llm)
    claim_ids = [c["id"] for c in claims]
    for c in claims:
        link_claim(conn, c["id"], c["text"], use_llm=use_llm)
    for cid in claim_ids:
        r = score_claim(conn, cid)
        conn.execute(
            "UPDATE claim SET confidence = ?, disputed = ? WHERE id = ?",
            (r["confidence"], int(r["disputed"]), cid),
        )
    conn.commit()

    response["claims"] = _claims_payload(conn, claim_ids)
    response["graph"] = graph_for_query(conn, query)

    if mode == "full":
        from .synthesize import synthesize

        result = synthesize(conn, query, response["claims"], use_llm=use_llm)
        response["answer"] = result["answer"]
        response["citations"] = result["sources"]
        response["meta"]["generator"] = result["generator"]

    response["meta"]["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.search", description="Unified /search pipeline")
    parser.add_argument("query")
    parser.add_argument("--mode", choices=MODES, default="raw")
    parser.add_argument("-k", type=int, default=DEFAULT_K)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from .index.vector import connect

    conn = connect()
    result = search(conn, args.query, mode=args.mode, k=args.k, use_llm=not args.no_llm)
    print(json.dumps(result, indent=2, default=str))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
