"""Store-as-cache policy: TTL, evidence-safe eviction, freshness."""

import pytest

from app import search as search_mod
from app.db import migrate
from app.index import vector
from app.live.pipeline import TTL_HOURS_BY_TIER, live_fetch
from tests.test_live_pipeline import (
    DEV_CANDS,
    PAGES,
    PG_URL,
    SO_URL,
    FakeFetcher,
    FakeProvider,
    _expire_ttl,
)

QUERY = "when does postgres autovacuum run"


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "live_cache.sqlite"
    migrate(db)
    c = vector.connect(db)
    yield c
    c.close()


def test_ttl_expiry_triggers_refetch(conn):
    fetcher = FakeFetcher(PAGES)
    live_fetch(conn, QUERY, provider=FakeProvider(DEV_CANDS), fetcher=fetcher)
    _expire_ttl(conn, PG_URL)
    report = live_fetch(conn, QUERY, provider=FakeProvider(DEV_CANDS), fetcher=fetcher)
    assert report["fresh"] == 1
    assert report["unchanged"] == 1
    assert fetcher.requests.count(PG_URL) == 2
    # the Stack Overflow question was fetched once, through the API (2 calls)
    assert sum("api.stackexchange.com" in r for r in fetcher.requests) == 2


def test_ttl_is_tier_aware():
    assert TTL_HOURS_BY_TIER["qa"] < TTL_HOURS_BY_TIER["docs"]
    assert TTL_HOURS_BY_TIER["repo"] < TTL_HOURS_BY_TIER["docs"]
    assert TTL_HOURS_BY_TIER["unknown"] <= TTL_HOURS_BY_TIER["registry"]


def _live_doc_count(conn) -> int:
    return conn.execute(
        "SELECT count(*) FROM document WHERE json_extract(metadata, '$.live') = 1"
    ).fetchone()[0]


def test_eviction_bounds_cache_and_spares_evidence(conn):
    fetcher = FakeFetcher(PAGES)
    live_fetch(conn, QUERY, provider=FakeProvider(DEV_CANDS), fetcher=fetcher)
    assert _live_doc_count(conn) == 2

    pg_chunk = conn.execute(
        "SELECT ch.id FROM chunk ch JOIN document d ON d.id = ch.document_id "
        "WHERE d.url = ? LIMIT 1", (PG_URL,),
    ).fetchone()[0]
    conn.execute("INSERT INTO claim (text, normalized_key) VALUES ('c', 'k')")
    claim_id = conn.execute("SELECT id FROM claim ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO claim_chunk (claim_id, chunk_id) VALUES (?, ?)", (claim_id, pg_chunk))
    conn.execute("UPDATE document SET fetched_at = datetime('now','-20 days') WHERE url = ?", (PG_URL,))
    conn.execute("UPDATE document SET fetched_at = datetime('now','-10 days') WHERE url = ?", (SO_URL,))
    conn.commit()

    report = live_fetch(conn, QUERY, provider=FakeProvider(DEV_CANDS), fetcher=fetcher,
                        cache_max_docs=1)
    assert report["evicted"] == 1
    urls = {r["url"] for r in conn.execute(
        "SELECT url FROM document WHERE json_extract(metadata, '$.live') = 1"
    )}
    assert PG_URL in urls
    n_chunks = conn.execute("SELECT count(*) FROM chunk").fetchone()[0]
    n_vec = conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0]
    assert n_chunks == n_vec


def test_eviction_disabled_by_default(conn):
    fetcher = FakeFetcher(PAGES)
    report = live_fetch(conn, QUERY, provider=FakeProvider(DEV_CANDS), fetcher=fetcher)
    assert report["evicted"] == 0
    assert _live_doc_count(conn) == 2


def test_sources_carry_fetched_at(conn):
    out = search_mod.search(
        conn, QUERY, mode="raw",
        live_provider=FakeProvider(DEV_CANDS), live_fetcher=FakeFetcher(PAGES),
    )
    assert out["sources"]
    assert all(s["fetched_at"] for s in out["sources"])
    assert out["meta"]["live"]["fresh"] == 0
