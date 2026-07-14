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

**Agent shaping (SUP-105).** Independent of mode, ``format`` and ``fields`` shape
the payload for the consumer:

- ``format="full"`` (default) — the UI contract, every top-level key present.
- ``format="agent"`` — compact: opaque handles (``chk_``/``clm_``) instead of
  bare rowids, evidence referenced by source handle instead of repeating the
  URL, and null/empty sections dropped.
- ``fields="sources,claims"`` — include only the named sections (``meta`` and
  the query echo are always kept). Omitted → mode's default sections.
- ``offset`` / cursor — paginate the ``sources`` list; ``meta.page`` carries the
  next cursor.

CLI:  ``uv run python -m app.search "Postgres vs MySQL" --mode full``
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sqlite3
import time

from . import ids
from .retrieve import retrieve

log = logging.getLogger("moo.search")

MODES = ("raw", "claims", "full")
FORMATS = ("full", "agent")
CONTRACT_VERSION = "1.1"
DEFAULT_K = 10

# Live retrieval page caps per mode (SUP-145): fast mode fetches fewer pages to
# stay fast; deep modes may pull more before the evidence pass.
LIVE_PAGES = {"raw": 4, "claims": 6, "full": 6}

# Top-level sections `fields` can select; `_ALWAYS` keys are never filtered out.
_SECTIONS = ("sources", "claims", "graph", "answer", "citations")
_ALWAYS = ("query", "mode", "intent", "meta")
# Sections included by default per mode when `fields` is not given.
_DEFAULT_FIELDS = {
    "raw": {"sources"},
    "claims": {"sources", "claims", "graph"},
    "full": {"sources", "claims", "graph", "answer", "citations"},
}


# ---------------------------------------------------------------------------
# pagination cursor (opaque base64 of the sources offset)
# ---------------------------------------------------------------------------

def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    pad = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + pad).decode()
    except Exception as e:  # noqa: BLE001 - any decode failure is a bad cursor
        raise ValueError(f"invalid cursor: {cursor!r}") from e
    if not raw.startswith("o:"):
        raise ValueError(f"invalid cursor: {cursor!r}")
    return int(raw[2:])


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
            # freshness (SUP-144): when this copy was fetched from the live web
            "fetched_at": getattr(h, "fetched_at", None),
            # injection-flagged content (SUP-131): treat as data, not instructions
            "suspicious": bool(getattr(h, "suspicious", False)),
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


# ---------------------------------------------------------------------------
# agent-shaped payload (compact, handle-addressed, evidence-by-reference)
# ---------------------------------------------------------------------------

def _agent_sources(sources: list[dict]) -> list[dict]:
    out = []
    for s in sources:
        row = {
            "id": ids.encode(ids.CHUNK, s["chunk_id"]),
            "url": s["url_anchor"] or s["document_url"],  # url_anchor is the deep link
            "source_type": s["source_type"],
            "score": s["score"],
        }
        if s.get("title"):
            row["title"] = s["title"]
        if s.get("trust_score") is not None:
            row["trust_score"] = s["trust_score"]
        if s.get("fetched_at"):
            row["fetched_at"] = s["fetched_at"]
        if s.get("suspicious"):
            row["suspicious"] = True
        out.append(row)
    return out


def _agent_claims(claims: list[dict]) -> list[dict]:
    out = []
    for c in claims:
        row: dict = {"id": ids.encode(ids.CLAIM, c["id"]), "text": c["text"]}
        if c.get("confidence") is not None:
            row["confidence"] = c["confidence"]
        if c.get("disputed"):
            row["disputed"] = True
        ev = []
        for e in c.get("evidence", []):
            edge = {"relation": e["relation"], "source": ids.encode(ids.CHUNK, e["chunk_id"])}
            if e.get("strength") is not None:
                edge["strength"] = e["strength"]
            ev.append(edge)
        if ev:
            row["evidence"] = ev
        out.append(row)
    return out


def _to_agent(response: dict, selected: set[str]) -> dict:
    """Compact projection of the full contract: handles instead of rowids,
    evidence referenced by source handle, null/empty sections dropped."""
    out: dict = {"query": response["query"], "mode": response["mode"], "intent": response["intent"]}
    if "sources" in selected:
        out["sources"] = _agent_sources(response["sources"])
    if "claims" in selected and response["claims"]:
        out["claims"] = _agent_claims(response["claims"])
    if "graph" in selected and (response["graph"]["nodes"] or response["graph"]["edges"]):
        out["graph"] = response["graph"]
    if "answer" in selected and response["answer"]:
        out["answer"] = response["answer"]
    if "citations" in selected and response["citations"]:
        out["citations"] = response["citations"]
    out["meta"] = response["meta"]
    return out


def _resolve_fields(fields: str | None, mode: str) -> set[str]:
    if not fields:
        return set(_DEFAULT_FIELDS[mode])
    requested = {f.strip() for f in fields.split(",") if f.strip()}
    unknown = requested - set(_SECTIONS)
    if unknown:
        raise ValueError(f"unknown fields {sorted(unknown)}; allowed: {list(_SECTIONS)}")
    return requested


def search(
    conn: sqlite3.Connection, query: str, *, mode: str = "raw", k: int = DEFAULT_K,
    use_llm: bool = True, format: str = "full", fields: str | None = None, offset: int = 0,
    live: bool | None = None, live_provider=None, live_fetcher=None,
) -> dict:
    """Run the pipeline to the depth `mode` requests and return the unified
    contract. `use_llm=False` forces every stage onto its heuristic path.

    `format`/`fields`/`offset` shape the payload for the consumer (see module
    docstring); the default `format="full"` with no `fields` is the unchanged
    v1.0 contract.

    **Live retrieval (SUP-145).** `live=None` (default) auto-refreshes the store
    from the live web before searching it, when a search provider is configured
    (`MOO_SEARXNG_URL` / `MOO_BRAVE_API_KEY`); `live=False` forces store-only.
    Live fetch makes **zero LLM calls**, so `mode=raw` + live is still the fast,
    model-free path — fast mode = live snippets, deep modes = live + evidence.
    What live retrieval did (or why it didn't run) lands in `meta.live`."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if format not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}, got {format!r}")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    selected = _resolve_fields(fields, mode)  # validate early, before any work
    started = time.perf_counter()

    from .understand import understand

    u = understand(query, conn)

    # ---- live retrieval: refresh the store before searching it ---------------
    live_report = None
    if live is not False:
        provider = live_provider
        if provider is None:
            from .live.providers import resolve_provider

            provider = resolve_provider()
        if provider is not None:
            from .live.pipeline import live_fetch

            try:
                live_report = live_fetch(
                    conn, query, max_pages=LIVE_PAGES[mode],
                    provider=provider, fetcher=live_fetcher,
                )
            except Exception as exc:  # noqa: BLE001 - degrade to the store, never fail
                log.warning("live fetch failed, serving from store: %s", exc)

    # ---- retrieval (every mode) --------------------------------------------
    if mode == "raw":
        # pure retrieval: heuristic fan-out only, guaranteed zero LLM calls
        from .expand import heuristic_expand

        variants = heuristic_expand(query)
    else:
        from .expand import expand

        variants = expand(conn, query, use_llm=use_llm)
    # Over-fetch one past the page so `has_more` is knowable, then slice.
    fetched = retrieve(conn, query, k=offset + k + 1, queries=variants, source_boost=u.source_boost)
    has_more = len(fetched) > offset + k
    hits = fetched[offset : offset + k]

    response: dict = {
        "query": query,
        "mode": mode,
        "intent": u.intent,
        "answer": None,
        "claims": [],
        "graph": {"nodes": [], "edges": []},
        "sources": _sources(hits),
        "citations": [],
        "meta": {
            "contract_version": CONTRACT_VERSION,
            "entities": u.entities,
            "page": {
                "offset": offset,
                "limit": k,
                "returned": len(hits),
                "has_more": has_more,
                "next_cursor": encode_cursor(offset + k) if has_more else None,
            },
        },
    }
    if live_report is not None:
        # compact summary; the shape is additive (absent when live is off)
        response["meta"]["live"] = {
            "provider": live_report["provider"],
            "out_of_domain": live_report["out_of_domain"],
            "domain_confidence": live_report["domain_confidence"],
            "fetched": live_report["fetched"],
            "fresh": live_report["fresh"],
            "unchanged": live_report["unchanged"],
            "failed": live_report["failed"],
            "timed_out": live_report["timed_out"],
            "evicted": live_report["evicted"],
            "new_chunks": live_report["new_chunks"],
            "timings_ms": live_report["timings_ms"],
            "cost": live_report["cost"],
        }

    if mode == "raw":
        return _finalize(response, started, format, fields, selected)

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

    return _finalize(response, started, format, fields, selected)


def _finalize(
    response: dict, started: float, format: str, fields: str | None, selected: set[str]
) -> dict:
    """Stamp elapsed time, then apply format + field selection. `full` format
    with no explicit `fields` is left untouched (the legacy v1.0 shape)."""
    response["meta"]["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    # SUP-131: when injection-flagged content is in the result set, say so
    # loudly at the top level (per-source rows carry the flag either way).
    if any(s.get("suspicious") for s in response.get("sources", [])):
        from .safety import UNTRUSTED_NOTICE

        response["meta"]["untrusted_content"] = True
        response["meta"]["content_notice"] = UNTRUSTED_NOTICE
    if format == "agent":
        return _to_agent(response, selected)
    if fields is not None:
        for section in _SECTIONS:
            if section not in selected:
                response.pop(section, None)
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.search", description="Unified /search pipeline")
    parser.add_argument("query")
    parser.add_argument("--mode", choices=MODES, default="raw")
    parser.add_argument("-k", type=int, default=DEFAULT_K)
    parser.add_argument("--format", choices=FORMATS, default="full")
    parser.add_argument("--fields", default=None, help="comma-separated sections, e.g. sources,claims")
    parser.add_argument("--offset", type=int, default=0, help="pagination offset into sources")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-live", action="store_true", help="store-only: skip live web fetch")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from .index.vector import connect

    conn = connect()
    result = search(
        conn, args.query, mode=args.mode, k=args.k, use_llm=not args.no_llm,
        format=args.format, fields=args.fields, offset=args.offset,
        live=False if args.no_live else None,
    )
    print(json.dumps(result, indent=2, default=str))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
