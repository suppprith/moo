"""Per-query live retrieval pipeline (SUP-130).

``live_fetch(conn, query)`` runs: discover candidate URLs (provider) -> rank
them with the software-domain policy -> fetch + extract each page -> write
through the standard ingest path (document upsert -> chunk -> embed -> vector +
keyword index). Everything lands in the same store the rest of moo already
searches, so:

- ``retrieve()`` immediately sees "cache + just-fetched" with no new code path;
- the evidence layer (claims/links/confidence) works over live pages unchanged;
- the store *is* the cache: re-running a query re-fetches through the Fetcher's
  ETag/Last-Modified cache and skips unchanged content via the document
  content-hash, so warm queries cost almost nothing (SUP-144 adds TTL policy).

Fetching is serial in this cut; SUP-146 adds per-host concurrency + deadlines.

CLI:  ``uv run python -m app.live "how does postgres vacuum work"``
"""

from __future__ import annotations

import logging
import sqlite3
import time

import trafilatura

from ..chunk import rechunk
from ..embed import embed_corpus
from ..index import keyword, vector
from ..ingest.base import store
from ..ingest.fetcher import Fetcher
from ..ingest.models import RawDoc
from . import policy
from .providers import Provider, resolve_provider

log = logging.getLogger("moo.live")

DEFAULT_MAX_PAGES = 6
DISCOVER_COUNT = 16  # candidates asked from the provider (pre-policy)

# One shared fetcher: keeps per-host politeness state + the on-disk HTTP cache
# warm across queries in the same process.
_FETCHER: Fetcher | None = None


def _default_fetcher() -> Fetcher:
    global _FETCHER
    if _FETCHER is None:
        # live path: shorter per-request timeout than batch ingest; robots on.
        _FETCHER = Fetcher(min_interval=0.5, timeout=10.0, obey_robots=True)
    return _FETCHER


def _extract(url: str, html: str, info: policy.DomainInfo) -> RawDoc | None:
    """HTML -> RawDoc via trafilatura (same extraction as the docs connector)."""
    md = trafilatura.extract(
        html, output_format="markdown", include_formatting=True, favor_recall=True
    )
    if not md or not md.strip():
        return None
    title = url
    published = author = None
    try:
        meta = trafilatura.bare_extraction(html, url=url, with_metadata=True)
        if meta is not None:
            title = getattr(meta, "title", None) or url
            published = getattr(meta, "date", None)
            author = getattr(meta, "author", None)
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        log.debug("metadata extraction failed for %s: %s", url, exc)
    return RawDoc(
        source_type=info.source_type,
        url=url,
        title=title,
        text=md,
        author=author,
        published_at=published,
        content_type="text/markdown",
        # never store the query on the document (no-query-logging principle)
        metadata={"live": True, "site": info.host, "tier": info.tier},
    )


def _doc_id(conn: sqlite3.Connection, url: str) -> int | None:
    row = conn.execute("SELECT id FROM document WHERE url = ?", (url,)).fetchone()
    return row["id"] if row else None


def _drop_chunks(conn: sqlite3.Connection, doc_id: int) -> None:
    """Delete a re-fetched document's chunks so rechunk(only_new) redoes them.
    Chunks elsewhere may hold canonical refs into this doc — detach them first
    (they stay searchable, just lose the dedup link)."""
    conn.execute(
        "UPDATE chunk SET canonical_chunk_id = NULL WHERE canonical_chunk_id IN "
        "(SELECT id FROM chunk WHERE document_id = ?)",
        (doc_id,),
    )
    conn.execute("DELETE FROM chunk WHERE document_id = ?", (doc_id,))


def live_fetch(
    conn: sqlite3.Connection,
    query: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    provider: Provider | None = None,
    fetcher: Fetcher | None = None,
) -> dict:
    """Discover + fetch live pages for ``query`` into the store.

    Returns a JSON-able report (also embedded in search ``meta.live``):
    ``{available, provider, out_of_domain, domain_confidence, discovered,
    considered[], fetched, unchanged, failed, new_docs, updated_docs,
    new_chunks, embedded, timings_ms{}}``.

    Requires a vec-enabled connection (``vector.connect()``) because new
    embeddings are synced into ``chunk_vec``.
    """
    report: dict = {
        "available": False,
        "provider": None,
        "out_of_domain": False,
        "domain_confidence": None,
        "discovered": 0,
        "considered": [],
        "fetched": 0,
        "unchanged": 0,
        "failed": 0,
        "new_docs": [],
        "updated_docs": [],
        "new_chunks": 0,
        "embedded": 0,
        "timings_ms": {},
    }

    provider = provider or resolve_provider()
    if provider is None:
        return report  # live search not configured; caller uses the store
    report["available"] = True
    report["provider"] = provider.name

    # -- discover -------------------------------------------------------------
    t0 = time.perf_counter()
    cands = provider.discover(query, count=DISCOVER_COUNT)
    report["timings_ms"]["discover"] = round((time.perf_counter() - t0) * 1000, 1)
    report["discovered"] = len(cands)
    if not cands:
        return report

    report["domain_confidence"] = round(policy.domain_confidence(cands), 3)
    if policy.out_of_domain(cands):
        # not a software question: don't fetch food blogs into the corpus
        report["out_of_domain"] = True
        return report

    ranked = policy.rank_candidates(cands, max_pages=max_pages)
    report["considered"] = [
        {"url": c.url, "tier": d.tier, "prior": d.prior} for c, d in ranked
    ]

    # -- fetch + extract + upsert ----------------------------------------------
    fetcher = fetcher or _default_fetcher()
    t0 = time.perf_counter()
    for cand, info in ranked:
        res = fetcher.get(cand.url)
        if not res.ok or not res.text:
            report["failed"] += 1
            continue
        doc = _extract(cand.url, res.text, info)
        if doc is None:
            report["failed"] += 1
            continue
        try:
            outcome = store(conn, doc)
        except Exception as exc:  # noqa: BLE001 - one bad page never kills the query
            log.warning("store failed for %s: %s", cand.url, exc)
            report["failed"] += 1
            continue
        did = _doc_id(conn, cand.url)
        if outcome == "inserted":
            report["fetched"] += 1
            report["new_docs"].append(did)
        elif outcome == "updated":
            report["fetched"] += 1
            report["updated_docs"].append(did)
            if did is not None:
                _drop_chunks(conn, did)  # content changed -> rechunk below
        else:  # unchanged: already cached, chunks/embeddings still valid
            report["unchanged"] += 1
    conn.commit()
    report["timings_ms"]["fetch"] = round((time.perf_counter() - t0) * 1000, 1)

    if not report["new_docs"] and not report["updated_docs"]:
        return report  # fully warm: nothing to chunk/embed/index

    # -- chunk -> embed -> index (all incremental) ------------------------------
    t0 = time.perf_counter()
    stats = rechunk(conn, only_new=True)
    report["new_chunks"] = stats["chunks"]
    report["timings_ms"]["chunk"] = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    report["embedded"] = embed_corpus(conn)["embedded"]
    report["timings_ms"]["embed"] = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    vector.build(conn)
    keyword.build(conn)
    report["timings_ms"]["index"] = round((time.perf_counter() - t0) * 1000, 1)
    return report
