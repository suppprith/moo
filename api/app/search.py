"""Unified search pipeline + response contract.

`search(conn, query, mode=...)` ties the whole pipeline together and returns the
single contract every client builds against:

    { query, mode, intent, answer, claims[], graph, sources[], citations, meta }

LLM work is opt-in via `mode`:

- ``raw``    (default) pure retrieval — understanding + heuristic fan-out +
             hybrid retrieval. **Zero LLM calls**, target < 1s.
- ``claims`` adds LLM rerank + claim extraction + evidence linking + confidence,
             plus the query subgraph.
- ``full``   adds a cited, synthesized answer.

Cost and latency scale with mode; ``raw`` never touches a model. Each heavier
stage still degrades to its heuristic fallback when no credentials are present,
so every mode runs end-to-end without a key (lower quality, same shape).

**Agent shaping.** Independent of mode, ``format`` and ``fields`` shape
the payload for the consumer:

- ``format="full"`` (default) — the UI contract, every top-level key present.
- ``format="agent"`` — compact: opaque handles (``chk_``/``clm_``) instead of
  bare rowids, evidence referenced by source handle instead of repeating the
  URL, and null/empty sections dropped.
- ``fields="sources,claims"`` — include only the named sections (``meta`` and
  the query echo are always kept). Omitted → mode's default sections.
- ``offset`` / cursor — paginate the ``sources`` list; ``meta.page`` carries the
  next cursor.

Every call reports what it spent: ``meta.timings_ms`` per stage and ``meta.cost``
(model calls, cache hits, and the input the calls carried), logged per request
against the mode's latency budget.

CLI:  ``uv run python -m app.search "Postgres vs MySQL" --mode full``
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sqlite3

from . import ids, llm
from .retrieve import retrieve
from .timing import Timings, budget_for

log = logging.getLogger("moo.search")

MODES = ("raw", "claims", "full")
FORMATS = ("full", "agent")
CONTRACT_VERSION = "1.1"
DEFAULT_K = 10

LIVE_PAGES = {"raw": 4, "claims": 6, "full": 6}

_SECTIONS = ("sources", "claims", "graph", "answer", "citations")
_ALWAYS = ("query", "mode", "intent", "meta")
_DEFAULT_FIELDS = {
    "raw": {"sources"},
    "claims": {"sources", "claims", "graph"},
    "full": {"sources", "claims", "graph", "answer", "citations"},
}


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    pad = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + pad).decode()
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"invalid cursor: {cursor!r}") from e
    if not raw.startswith("o:"):
        raise ValueError(f"invalid cursor: {cursor!r}")
    return int(raw[2:])


def _sources(hits: list, version_notes: dict[int, dict] | None = None) -> list[dict]:
    out = []
    for h in hits:
        row = {
            "chunk_id": h.chunk_id,
            "document_url": h.document_url,
            "title": h.title,
            "source_type": h.source_type,
            "trust_score": h.trust_score,
            "heading": h.heading,
            "url_anchor": h.url_anchor,
            "score": round(h.score, 6),
            "fetched_at": getattr(h, "fetched_at", None),
            "suspicious": bool(getattr(h, "suspicious", False)),
        }
        if getattr(h, "rank_signals", None):
            row["rank_signals"] = h.rank_signals
        note = (version_notes or {}).get(h.chunk_id)
        if note is not None:
            row["version_match"] = note["match"]
            row["version_outdated"] = note["outdated"]
            if note["mentions"]:
                row["versions"] = note["mentions"]
        out.append(row)
    return out


def _claims_payload(conn: sqlite3.Connection, claim_ids: list[int]) -> list[dict]:
    """Reload the given claims with confidence + their evidence edges."""
    if not claim_ids:
        return []
    from .evidence.temporal import superseded_by

    qmarks = ",".join("?" * len(claim_ids))
    claims = conn.execute(
        f"SELECT id, text, confidence, disputed, valid_product, valid_from, valid_until "
        f"FROM claim WHERE id IN ({qmarks})", claim_ids
    ).fetchall()
    superseders = superseded_by(conn, claim_ids)
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
        row = {
            "id": c["id"], "text": c["text"],
            "confidence": c["confidence"], "disputed": bool(c["disputed"]),
            "evidence": by_claim.get(c["id"], []),
        }
        if c["valid_product"]:
            row["valid"] = {
                "product": c["valid_product"],
                "from": c["valid_from"],
                "until": c["valid_until"],
            }
        if c["id"] in superseders:
            row["superseded_by"] = superseders[c["id"]]
        out.append(row)
    return out


def _agent_sources(sources: list[dict]) -> list[dict]:
    out = []
    for s in sources:
        row = {
            "id": ids.encode(ids.CHUNK, s["chunk_id"]),
            "url": s["url_anchor"] or s["document_url"],
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
        if s.get("version_match") is not None:
            row["version_match"] = s["version_match"]
            if s.get("version_outdated"):
                row["version_outdated"] = True
        if s.get("highlights"):
            row["highlights"] = s["highlights"]
            row["relevance"] = s["relevance"]
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
        if c.get("valid"):
            row["valid"] = c["valid"]
        if c.get("superseded_by"):
            row["superseded_by"] = ids.encode(ids.CLAIM, c["superseded_by"])
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
    highlights: bool = False,
) -> dict:
    """Run the pipeline to the depth `mode` requests and return the unified
    contract. `use_llm=False` forces every stage onto its heuristic path.

    `format`/`fields`/`offset` shape the payload for the consumer (see module
    docstring); the default `format="full"` with no `fields` is the unchanged
    v1.0 contract.

    **Live retrieval.** `live=None` (default) auto-refreshes the store
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
    selected = _resolve_fields(fields, mode)
    t = Timings(counter=llm.attempts)
    llm_before = llm.get_stats()

    from . import queryops

    ops = queryops.parse(query)
    retrieval_query = ops.text or query

    from .understand import understand

    with t.stage("understand"):
        u = understand(retrieval_query, conn)

    live_report = None
    if live is not False:
        provider = live_provider
        if provider is None:
            from .live.providers import resolve_provider

            provider = resolve_provider()
        if provider is not None:
            from .live.pipeline import live_fetch

            with t.stage("live"):
                try:
                    live_report = live_fetch(
                        conn, retrieval_query, max_pages=LIVE_PAGES[mode],
                        provider=provider, fetcher=live_fetcher,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("live fetch failed, serving from store: %s", exc)

    from .routing import route

    routing = route(retrieval_query, u.intent)

    with t.stage("expand"):
        if not routing.fan_out:
            variants = []
        elif mode == "raw":
            from .expand import heuristic_expand

            variants = heuristic_expand(retrieval_query)
        else:
            from .expand import expand

            variants = expand(conn, retrieval_query, use_llm=use_llm)
    fetch_k = (offset + k + 1) * (4 if ops.has_post_filters else 1)
    with t.stage("retrieve"):
        fetched = retrieve(
            conn, retrieval_query, k=fetch_k, queries=variants, source_boost=u.source_boost,
            source_types=ops.source_types, since=ops.since,
            index_weights=(routing.vector_weight, routing.keyword_weight),
        )
        if ops.has_post_filters:
            fetched = [
                h for h in fetched
                if queryops.matches(ops, text=h.text, url=h.url_anchor or h.document_url)
            ]

        from . import versions as versions_mod

        version_constraint = versions_mod.query_constraint(retrieval_query)
        version_notes: dict[int, dict] = {}
        if version_constraint is not None:
            version_notes = versions_mod.apply_constraint(fetched, version_constraint)

    has_more = len(fetched) > offset + k
    hits = fetched[offset : offset + k]

    response: dict = {
        "query": query,
        "mode": mode,
        "intent": u.intent,
        "answer": None,
        "claims": [],
        "graph": {"nodes": [], "edges": []},
        "sources": _sources(hits, version_notes),
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
    if version_constraint is not None:
        response["meta"]["version_constraint"] = {
            "product": version_constraint.product,
            "version": version_constraint.version,
        }
    if ops.active or ops.invalid:
        response["meta"]["operators"] = ops.describe()
    response["meta"]["routing"] = {
        "strategy": routing.strategy,
        "vector_weight": routing.vector_weight,
        "keyword_weight": routing.keyword_weight,
        "fan_out": routing.fan_out,
        "signals": routing.signals,
    }
    if live_report is not None:
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
        if highlights:
            with t.stage("highlights"):
                _attach_highlights(retrieval_query, hits, response["sources"])
        return _finalize(response, t, llm_before, format, fields, selected)

    from .evidence.claims import extract_claims
    from .evidence.confidence import score_claim
    from .evidence.links import link_claim
    from .graph.query import graph_for_query
    from .rerank import rerank

    with t.stage("rerank"):
        hits = rerank(conn, retrieval_query, hits, use_llm=use_llm)
    response["sources"] = _sources(hits, version_notes)
    if highlights:
        with t.stage("highlights"):
            _attach_highlights(retrieval_query, hits, response["sources"])

    with t.stage("claims"):
        claims = extract_claims(conn, retrieval_query, k=k, use_llm=use_llm)
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

        from .evidence.temporal import process as temporal_process

        temporal_process(conn, claims)

        response["claims"] = _claims_payload(conn, claim_ids)

    with t.stage("graph"):
        response["graph"] = graph_for_query(conn, retrieval_query)

    if mode == "full":
        from .synthesize import synthesize

        with t.stage("synthesize"):
            result = synthesize(conn, retrieval_query, response["claims"], use_llm=use_llm)
        response["answer"] = result["answer"]
        response["citations"] = result["sources"]
        response["meta"]["generator"] = result["generator"]

    return _finalize(response, t, llm_before, format, fields, selected)


def _attach_highlights(query: str, hits: list, source_rows: list[dict]) -> None:
    """Enrich source rows with best-span highlights + calibrated relevance
. Opt-in via search(highlights=True); /v1/web_search always on."""
    from .highlights import highlight_hits

    for row, hl in zip(source_rows, highlight_hits(query, [h.text for h in hits]), strict=True):
        row["highlights"] = hl["highlights"]
        row["relevance"] = hl["relevance"]


def _cost(t: Timings, before: dict) -> dict:
    """What this search spent on models. `llm_calls` is real provider requests;
    `llm_attempts` counts stages that wanted one, so a keyless or fully cached
    run still reports its model demand (attempts minus calls is what the cache
    and the heuristic fallbacks absorbed)."""
    after = llm.get_stats()
    cost = {
        "llm_calls": after["calls"] - before["calls"],
        "llm_attempts": after["attempts"] - before["attempts"],
        "cache_hits": after["cache_hits"] - before["cache_hits"],
        "cache_misses": after["cache_misses"] - before["cache_misses"],
        "prompt_chars": after["prompt_chars"] - before["prompt_chars"],
    }
    by_stage = t.llm_by_stage()
    if by_stage:
        cost["by_stage"] = by_stage
    return cost


def _finalize(
    response: dict, t: Timings, llm_before: dict, format: str, fields: str | None,
    selected: set[str],
) -> dict:
    """Stamp timings + cost, then apply format + field selection. `full` format
    with no explicit `fields` is left untouched (the legacy v1.0 shape)."""
    mode = response["mode"]
    response["meta"]["elapsed_ms"] = t.report(f"search mode={mode}", budget_for(mode))
    response["meta"]["timings_ms"] = t.as_dict()
    response["meta"]["cost"] = _cost(t, llm_before)
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
