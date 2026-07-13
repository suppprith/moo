"""Per-query live retrieval pipeline (SUP-130, perf pass SUP-146).

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

**Concurrency + budget (SUP-146).** Network fetch is the latency floor of live
mode, so pages are fetched **concurrently across hosts** while every host's own
requests stay serial in one worker — per-host politeness (Fetcher throttle,
robots) is preserved exactly. A wall-clock deadline (``max_seconds``, env
``MOO_LIVE_MAX_SECONDS``) bounds the whole fetch stage: when it expires the
pipeline returns **partial results** (whatever finished) instead of blocking on
stragglers; unfinished pages are counted in ``timed_out``. Fetch + extraction
run in workers; all SQLite writes stay on the calling thread (sqlite3
connections are not thread-safe). ``report.cost`` accounts provider calls and
pages attempted so per-query external cost is always visible.

CLI:  ``uv run python -m app.live "how does postgres vacuum work"``
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from urllib.parse import urlsplit

import trafilatura

from ..chunk import rechunk
from ..embed import embed_corpus
from ..index import keyword, vector
from ..ingest.base import store
from ..ingest.fetcher import Fetcher
from ..ingest.models import RawDoc
from . import policy
from .providers import Provider, get_stats, resolve_provider

log = logging.getLogger("moo.live")

DEFAULT_MAX_PAGES = 6
DISCOVER_COUNT = 16  # candidates asked from the provider (pre-policy)
DEFAULT_MAX_SECONDS = float(os.environ.get("MOO_LIVE_MAX_SECONDS", "12"))
MAX_WORKERS = 6      # concurrent host workers (one host is never parallelized)

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


def _fetch_extract_all(
    fetcher, ranked: list, deadline: float
) -> tuple[list[tuple[int, object, policy.DomainInfo, RawDoc | None]], int]:
    """Fetch + extract candidates concurrently across hosts, serially within a
    host. One future per page; a per-host lock serializes same-host requests so
    the Fetcher's politeness (throttle/robots) is preserved exactly. Results
    are collected page-by-page, so a deadline expiry keeps everything already
    finished (partial results) and abandons only the stragglers."""
    hosts = {urlsplit(cand.url).netloc for cand, _ in ranked}
    host_locks = {h: threading.Lock() for h in hosts}

    def page_worker(idx: int, cand, info: policy.DomainInfo):
        lock = host_locks[urlsplit(cand.url).netloc]
        with lock:  # same-host requests never overlap
            if time.monotonic() > deadline:
                return None  # queued behind slower same-host pages: timed out
            res = fetcher.get(cand.url)
        doc = None
        if res.ok and res.text:
            doc = _extract(cand.url, res.text, info)  # CPU work outside the lock
        return (idx, cand, info, doc)

    done_items: list[tuple[int, object, policy.DomainInfo, RawDoc | None]] = []
    executor = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(ranked)))
    try:
        pending: set[Future] = {
            executor.submit(page_worker, idx, cand, info)
            for idx, (cand, info) in enumerate(ranked)
        }
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break  # abandon stragglers; partial results beat blocking
            finished, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            for fut in finished:
                try:
                    item = fut.result()
                except Exception as exc:  # noqa: BLE001 - one page must not kill the query
                    log.warning("page worker failed: %s", exc)
                    continue
                if item is not None:
                    done_items.append(item)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    timed_out = len(ranked) - len(done_items)
    done_items.sort(key=lambda t: t[0])  # deterministic store order by rank
    return done_items, timed_out


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
    max_seconds: float = DEFAULT_MAX_SECONDS,
    provider: Provider | None = None,
    fetcher: Fetcher | None = None,
) -> dict:
    """Discover + fetch live pages for ``query`` into the store.

    ``max_pages`` caps pages fetched; ``max_seconds`` (env
    ``MOO_LIVE_MAX_SECONDS``) is a wall-clock deadline on the fetch stage —
    on expiry the pipeline continues with whatever finished (partial results),
    counting the rest in ``timed_out``.

    Returns a JSON-able report (also embedded in search ``meta.live``):
    ``{available, provider, out_of_domain, domain_confidence, discovered,
    considered[], fetched, unchanged, failed, timed_out, new_docs,
    updated_docs, new_chunks, embedded, timings_ms{}, cost{}}``.

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
        "timed_out": 0,
        "new_docs": [],
        "updated_docs": [],
        "new_chunks": 0,
        "embedded": 0,
        "timings_ms": {},
        "cost": {"provider_calls": 0, "pages_attempted": 0, "max_seconds": max_seconds},
    }

    provider = provider or resolve_provider()
    if provider is None:
        return report  # live search not configured; caller uses the store
    report["available"] = True
    report["provider"] = provider.name

    # -- discover -------------------------------------------------------------
    t0 = time.perf_counter()
    calls_before = get_stats()["calls"]
    cands = provider.discover(query, count=DISCOVER_COUNT)
    report["cost"]["provider_calls"] = get_stats()["calls"] - calls_before
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

    # -- fetch + extract (concurrent, deadline-bounded) -------------------------
    fetcher = fetcher or _default_fetcher()
    t0 = time.perf_counter()
    report["cost"]["pages_attempted"] = len(ranked)
    deadline = time.monotonic() + max_seconds
    results, report["timed_out"] = _fetch_extract_all(fetcher, ranked, deadline)

    # -- upsert (main thread: sqlite3 connections are not thread-safe) ----------
    for _idx, cand, _info, doc in results:
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
