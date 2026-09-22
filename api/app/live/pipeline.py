"""Per-query live retrieval pipeline.

``live_fetch(conn, query)`` runs: discover candidate URLs (provider) -> rank
them with the software-domain policy -> fetch + extract each page -> write
through the standard ingest path (document upsert -> chunk -> embed -> vector +
keyword index). Everything lands in the same store the rest of moo already
searches, so:

- ``retrieve()`` immediately sees "cache + just-fetched" with no new code path;
- the evidence layer (claims/links/confidence) works over live pages unchanged;
- the store *is* the cache: a document fetched within its tier's TTL
  is served with **zero network work**; past TTL it re-fetches through the
  Fetcher's ETag cache and skips unchanged content via the content-hash. The
  live-doc cache can be bounded (``MOO_CACHE_MAX_DOCS``) with eviction that
  never touches documents backing claims/evidence — the graph accumulates.

**Concurrency + budget.** Network fetch is the latency floor of live
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

import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from urllib.parse import urlsplit

import trafilatura

from .. import safety
from ..chunk import rechunk
from ..embed import embed_corpus
from ..evidence import trust
from ..index import keyword, vector
from ..ingest.base import store
from ..ingest.fetcher import Fetcher
from ..ingest.models import RawDoc
from . import policy, stackexchange
from .providers import Provider, get_stats, resolve_provider

log = logging.getLogger("moo.live")

DEFAULT_MAX_PAGES = 6
DISCOVER_COUNT = 16
DEFAULT_MAX_SECONDS = float(os.environ.get("MOO_LIVE_MAX_SECONDS", "12"))
MAX_WORKERS = 6

TTL_HOURS_BY_TIER = {
    "docs": 168,
    "repo": 24,
    "registry": 72,
    "qa": 24,
    "blog": 168,
    "unknown": 24,
}

CACHE_MAX_DOCS = int(os.environ.get("MOO_CACHE_MAX_DOCS", "0"))

_FETCHER: Fetcher | None = None


def _default_fetcher() -> Fetcher:
    global _FETCHER
    if _FETCHER is None:
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
    except Exception as exc:  # noqa: BLE001
        log.debug("metadata extraction failed for %s: %s", url, exc)
    return _mark_live(RawDoc(
        source_type=info.source_type,
        url=url,
        title=title,
        text=md,
        author=author,
        published_at=published,
        content_type="text/markdown",
    ), info)


def _mark_live(doc: RawDoc, info: policy.DomainInfo) -> RawDoc:
    """Stamp a fetched document as live and screen it for injected instructions,
    however it was fetched."""
    doc.metadata = {**(doc.metadata or {}), "live": True, "site": info.host, "tier": info.tier}
    cats = safety.categories(doc.text)
    if cats:
        doc.metadata["suspicious"] = cats
        log.warning("suspicious content (%s) at %s", ",".join(cats), doc.url)
    return doc


def _fetch_extract_all(
    fetcher, ranked: list, deadline: float
) -> tuple[list[tuple[int, object, policy.DomainInfo, RawDoc | None]], int]:
    """Fetch + extract candidates concurrently across hosts, serially within a
    host. One future per page; a per-host lock serializes same-host requests so
    the Fetcher's politeness (throttle/robots) is preserved exactly. Results
    are collected page-by-page, so a deadline expiry keeps everything already
    finished (partial results) and abandons only the stragglers.

    Stack Exchange questions skip the HTML fetch, which those sites refuse: one
    future per site fetches all of that site's questions through the API."""
    se_groups: dict[str, list[tuple[int, object, policy.DomainInfo, int]]] = {}
    pages = []
    for idx, (cand, info) in enumerate(ranked):
        ref = stackexchange.question_ref(cand.url)
        if ref is None:
            pages.append((idx, cand, info))
        else:
            se_groups.setdefault(ref[0], []).append((idx, cand, info, ref[1]))

    hosts = {urlsplit(cand.url).netloc for _, cand, _ in pages}
    host_locks = {h: threading.Lock() for h in hosts}
    se_lock = threading.Lock()  # every site shares one API host

    def page_worker(idx: int, cand, info: policy.DomainInfo):
        lock = host_locks[urlsplit(cand.url).netloc]
        with lock:
            if time.monotonic() > deadline:
                return None
            res = fetcher.get(cand.url)
        doc = None
        if res.ok and res.text:
            doc = _extract(cand.url, res.text, info)
        return [(idx, cand, info, doc)]

    def site_worker(site: str, group: list):
        with se_lock:
            if time.monotonic() > deadline:
                return None
            docs = stackexchange.fetch_questions(
                fetcher, site, [qid for *_, qid in group],
                urls={qid: cand.url for _, cand, _, qid in group},
            )
        return [(idx, cand, info, _mark_live(docs[qid], info) if qid in docs else None)
                for idx, cand, info, qid in group]

    done_items: list[tuple[int, object, policy.DomainInfo, RawDoc | None]] = []
    workers = max(1, min(MAX_WORKERS, len(pages) + len(se_groups)))
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        pending: set[Future] = {
            executor.submit(page_worker, idx, cand, info) for idx, cand, info in pages
        }
        pending |= {executor.submit(site_worker, site, group)
                    for site, group in se_groups.items()}
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            finished, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            for fut in finished:
                try:
                    item = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.warning("page worker failed: %s", exc)
                    continue
                if item is not None:
                    done_items.extend(item)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    timed_out = len(ranked) - len(done_items)
    done_items.sort(key=lambda t: t[0])
    return done_items, timed_out


def _doc_id(conn: sqlite3.Connection, url: str) -> int | None:
    row = conn.execute("SELECT id FROM document WHERE url = ?", (url,)).fetchone()
    return row["id"] if row else None


def _score_trust(conn: sqlite3.Connection, doc_ids: list[int]) -> int:
    """Give freshly ingested live docs a trust score (the offline `score_all`
    job never sees them), halving it for suspicious content. Returns docs
    flagged suspicious."""
    flagged = 0
    for did in doc_ids:
        row = conn.execute("SELECT * FROM document WHERE id = ?", (did,)).fetchone()
        if row is None:
            continue
        score, _ = trust.trust_score(dict(row))
        meta = row["metadata"] or "{}"
        try:
            suspicious = bool(json.loads(meta).get("suspicious"))
        except ValueError:
            suspicious = False
        if suspicious:
            score *= safety.TRUST_PENALTY
            flagged += 1
        conn.execute("UPDATE document SET trust_score = ? WHERE id = ?",
                     (round(score, 4), did))
    return flagged


def _is_fresh(conn: sqlite3.Connection, url: str, tier: str) -> bool:
    """True when the stored copy of ``url`` is within its tier's TTL — serve it
    from the cache with zero network work."""
    row = conn.execute(
        "SELECT (julianday('now') - julianday(fetched_at)) * 24 AS age_hours "
        "FROM document WHERE url = ? AND fetched_at IS NOT NULL",
        (url,),
    ).fetchone()
    if row is None or row["age_hours"] is None:
        return False
    return row["age_hours"] < TTL_HOURS_BY_TIER.get(tier, TTL_HOURS_BY_TIER["unknown"])


def _evict(conn: sqlite3.Connection, cap: int) -> int:
    """Bound the live-fetched cache to ``cap`` documents. Evicts oldest-fetched
    first, skipping any document whose chunks back a claim or evidence edge —
    the accumulated evidence graph is never damaged. Returns docs evicted.

    Runs before the index step so vector.build()'s orphan pruning and the FTS
    rebuild clean up the deleted chunks in the same pass."""
    if cap <= 0:
        return 0
    live_count = conn.execute(
        "SELECT count(*) FROM document WHERE json_extract(metadata, '$.live') = 1"
    ).fetchone()[0]
    excess = live_count - cap
    if excess <= 0:
        return 0
    victims = [
        r["id"]
        for r in conn.execute(
            """
            SELECT d.id FROM document d
            WHERE json_extract(d.metadata, '$.live') = 1
              AND NOT EXISTS (SELECT 1 FROM claim_chunk cc
                              JOIN chunk ch ON ch.id = cc.chunk_id
                              WHERE ch.document_id = d.id)
              AND NOT EXISTS (SELECT 1 FROM evidence e
                              JOIN chunk ch ON ch.id = e.chunk_id
                              WHERE ch.document_id = d.id)
            ORDER BY d.fetched_at ASC
            LIMIT ?
            """,
            (excess,),
        )
    ]
    for did in victims:
        _drop_chunks(conn, did)
        conn.execute("DELETE FROM document WHERE id = ?", (did,))
    conn.commit()
    if victims:
        log.info("evicted %d live docs (cache cap %d)", len(victims), cap)
    return len(victims)


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
    cache_max_docs: int | None = None,
) -> dict:
    """Discover + fetch live pages for ``query`` into the store.

    ``max_pages`` caps pages fetched; ``max_seconds`` (env
    ``MOO_LIVE_MAX_SECONDS``) is a wall-clock deadline on the fetch stage —
    on expiry the pipeline continues with whatever finished (partial results),
    counting the rest in ``timed_out``. Candidates whose stored copy is within
    its tier's TTL are served from the cache with zero network work
    (``fresh``); ``cache_max_docs`` (env ``MOO_CACHE_MAX_DOCS``, 0 = unbounded)
    bounds the live-doc cache via evidence-safe eviction.

    Returns a JSON-able report (also embedded in search ``meta.live``):
    ``{available, provider, out_of_domain, domain_confidence, discovered,
    considered[], fresh, fetched, unchanged, failed, timed_out, evicted,
    new_docs, updated_docs, new_chunks, embedded, timings_ms{}, cost{}}``.

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
        "fresh": 0,
        "fetched": 0,
        "unchanged": 0,
        "failed": 0,
        "timed_out": 0,
        "evicted": 0,
        "suspicious": 0,
        "new_docs": [],
        "updated_docs": [],
        "new_chunks": 0,
        "embedded": 0,
        "timings_ms": {},
        "cost": {"provider_calls": 0, "pages_attempted": 0, "max_seconds": max_seconds},
    }

    provider = provider or resolve_provider()
    if provider is None:
        return report
    report["available"] = True
    report["provider"] = provider.name

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
        report["out_of_domain"] = True
        return report

    ranked = policy.rank_candidates(cands, max_pages=max_pages)
    report["considered"] = [
        {"url": c.url, "tier": d.tier, "prior": d.prior} for c, d in ranked
    ]

    to_fetch = []
    for cand, info in ranked:
        if _is_fresh(conn, cand.url, info.tier):
            report["fresh"] += 1
        else:
            to_fetch.append((cand, info))

    fetcher = fetcher or _default_fetcher()
    t0 = time.perf_counter()
    report["cost"]["pages_attempted"] = len(to_fetch)
    deadline = time.monotonic() + max_seconds
    if to_fetch:
        results, report["timed_out"] = _fetch_extract_all(fetcher, to_fetch, deadline)
    else:
        results = []

    for _idx, cand, _info, doc in results:  # main thread: sqlite3 conns aren't thread-safe
        if doc is None:
            report["failed"] += 1
            continue
        try:
            outcome = store(conn, doc)
        except Exception as exc:  # noqa: BLE001
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
                _drop_chunks(conn, did)
        else:
            report["unchanged"] += 1
    report["suspicious"] = _score_trust(
        conn, [d for d in report["new_docs"] + report["updated_docs"] if d is not None]
    )
    conn.commit()
    report["timings_ms"]["fetch"] = round((time.perf_counter() - t0) * 1000, 1)

    cap = CACHE_MAX_DOCS if cache_max_docs is None else cache_max_docs
    report["evicted"] = _evict(conn, cap)

    if not report["new_docs"] and not report["updated_docs"] and not report["evicted"]:
        return report

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
