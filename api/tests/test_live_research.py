"""Deep research over live-fetched pages.

run_research with an injected fake provider/fetcher: every loop step live-fetches
its query first, evidence is extracted from the refreshed store, and the report
stays grounded — no pre-built corpus involved.
"""

import pytest

from app.db import migrate
from app.index import vector
from app.research.report import assemble_report
from app.research.session import LIVE_PAGES_TOTAL, run_research
from tests.test_live_pipeline import (
    DEV_CANDS,
    FOOD_CANDS,
    PAGES,
    FakeFetcher,
    FakeProvider,
)

QUESTION = "when does postgres autovacuum actually run"


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "live_research.sqlite"
    migrate(db)
    c = vector.connect(db)
    yield c
    c.close()


def test_run_research_live_end_to_end(conn):
    fetcher = FakeFetcher(PAGES)
    run = run_research(
        conn, QUESTION, use_llm=False, max_steps=4,
        live_provider=FakeProvider(DEV_CANDS), live_fetcher=fetcher,
    )
    assert run["status"] in ("done", "partial")
    assert run["claims"], "live research produced no claims"

    urls = {
        row["url"]
        for c in run["claims"]
        for row in conn.execute(
            "SELECT d.url AS url FROM claim_chunk cc "
            "JOIN chunk ch ON ch.id = cc.chunk_id "
            "JOIN document d ON d.id = ch.document_id WHERE cc.claim_id = ?",
            (c["id"],),
        )
    }
    assert urls and urls <= set(PAGES)

    live = run["cost"]["live"]
    assert live["provider"] == "fake"
    assert live["steps"] >= 1
    assert live["pages_fetched"] >= 2
    assert live["pages_left"] >= 0
    assert live["pages_budget"] == LIVE_PAGES_TOTAL
    assert fetcher.requests, "no live fetches happened"


def test_live_report_is_grounded(conn):
    run = run_research(
        conn, QUESTION, use_llm=False, max_steps=3,
        live_provider=FakeProvider(DEV_CANDS), live_fetcher=FakeFetcher(PAGES),
    )
    report = assemble_report(conn, run, use_llm=False)
    g = report["groundedness"]
    assert g["findings_total"] >= 1
    assert g["ungrounded"] == []
    assert report["sources"], "report has no sources"
    assert {s["url"].split("#")[0] for s in report["sources"]} <= set(PAGES)


def test_live_false_disables_fetching(conn):
    fetcher = FakeFetcher(PAGES)
    run = run_research(
        conn, QUESTION, use_llm=False, max_steps=2,
        live=False, live_provider=FakeProvider(DEV_CANDS), live_fetcher=fetcher,
    )
    assert "live" not in run["cost"]
    assert fetcher.requests == []


def test_live_auto_off_without_provider(conn, monkeypatch):
    for var in ("MOO_SEARCH_PROVIDER", "MOO_SEARXNG_URL", "MOO_SEARCH_URL",
                "MOO_BRAVE_API_KEY", "MOO_SEARCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    run = run_research(conn, QUESTION, use_llm=False, max_steps=2)
    assert "live" not in run["cost"]


def test_out_of_domain_steps_are_counted_not_fetched(conn):
    fetcher = FakeFetcher({})
    run = run_research(
        conn, "best pizza dough recipe", use_llm=False, max_steps=2,
        live_provider=FakeProvider(FOOD_CANDS), live_fetcher=fetcher,
    )
    live = run["cost"]["live"]
    assert live["out_of_domain_steps"] >= 1
    assert live["pages_fetched"] == 0 and fetcher.requests == []
