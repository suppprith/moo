"""Fast vs deep mode over live retrieval (SUP-145).

fast = mode raw + live: fetch fresh pages, return snippets, ZERO LLM calls.
deep = mode claims/full + live: same fetch, then the evidence pass.
"""

import pytest

from app import llm
from app import search as search_mod
from app.db import migrate
from app.index import vector
from app.websearch import web_search
from tests.test_live_pipeline import (
    DEV_CANDS,
    FOOD_CANDS,
    PAGES,
    FakeFetcher,
    FakeProvider,
)

QUERY = "when does postgres autovacuum run"


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "live_modes.sqlite"
    migrate(db)
    c = vector.connect(db)
    yield c
    c.close()


@pytest.fixture
def llm_calls(monkeypatch):
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return None

    monkeypatch.setattr(llm, "generate_json", counting)
    return calls


def test_fast_mode_live_snippets_zero_llm(conn, llm_calls):
    out = search_mod.search(
        conn, QUERY, mode="raw",
        live_provider=FakeProvider(DEV_CANDS), live_fetcher=FakeFetcher(PAGES),
    )
    assert llm_calls["n"] == 0                       # fast mode stays model-free
    assert out["sources"], "live fast mode returned nothing"
    assert out["meta"]["live"]["fetched"] == 2
    assert out["meta"]["live"]["provider"] == "fake"
    assert out["meta"]["live"]["out_of_domain"] is False
    urls = {s["document_url"] for s in out["sources"]}
    assert urls <= set(PAGES)                        # everything came from the live fetch


def test_deep_mode_live_evidence(conn):
    out = search_mod.search(
        conn, QUERY, mode="claims", use_llm=False,
        live_provider=FakeProvider(DEV_CANDS), live_fetcher=FakeFetcher(PAGES),
    )
    assert out["claims"], "deep mode produced no claims over live pages"
    assert out["meta"]["live"]["fetched"] == 2


def test_live_false_serves_store_only(conn, llm_calls):
    fetcher = FakeFetcher(PAGES)
    out = search_mod.search(
        conn, QUERY, mode="raw", live=False,
        live_provider=FakeProvider(DEV_CANDS), live_fetcher=fetcher,
    )
    assert "live" not in out["meta"]                 # legacy shape untouched
    assert fetcher.requests == []


def test_live_auto_off_without_provider(conn, monkeypatch):
    for var in ("MOO_SEARCH_PROVIDER", "MOO_SEARXNG_URL", "MOO_SEARCH_URL",
                "MOO_BRAVE_API_KEY", "MOO_SEARCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    out = search_mod.search(conn, QUERY, mode="raw")
    assert "live" not in out["meta"]


def test_out_of_domain_flagged(conn):
    out = search_mod.search(
        conn, "best pizza dough recipe", mode="raw",
        live_provider=FakeProvider(FOOD_CANDS), live_fetcher=FakeFetcher({}),
    )
    assert out["meta"]["live"]["out_of_domain"] is True
    assert out["sources"] == []                      # nothing junk was ingested


def test_agent_format_carries_live_meta(conn):
    out = search_mod.search(
        conn, QUERY, mode="raw", format="agent",
        live_provider=FakeProvider(DEV_CANDS), live_fetcher=FakeFetcher(PAGES),
    )
    assert out["meta"]["live"]["provider"] == "fake"
    assert all(s["id"].startswith("chk_") for s in out["sources"])


def test_web_search_live_passthrough(conn):
    out = web_search(
        conn, QUERY, k=5, depth="raw",
        live_provider=FakeProvider(DEV_CANDS), live_fetcher=FakeFetcher(PAGES),
    )
    assert out["live"] == {"provider": "fake", "out_of_domain": False, "fetched": 2}
    assert out["results"] and all("url" in r and "snippet" in r for r in out["results"])
