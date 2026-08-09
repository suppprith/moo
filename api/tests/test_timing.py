"""Per-stage timings, budgets, and the cost block on a search response."""

import logging
import sqlite3

import pytest

from app import search as search_mod
from app.timing import BUDGET_MS, Timings, budget_for
from tests.test_search import FakeHit


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(search_mod, "retrieve", lambda *a, **k: [FakeHit(1), FakeHit(2)])
    return monkeypatch


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_stage_records_time_and_llm_attribution():
    counter = {"n": 0}
    t = Timings(counter=lambda: counter["n"])
    with t.stage("expand"):
        counter["n"] += 2
    with t.stage("retrieve"):
        pass
    assert set(t.as_dict()) == {"expand", "retrieve"}
    assert t.llm_by_stage() == {"expand": 2}
    assert t.elapsed_ms >= 0


def test_repeated_stage_accumulates():
    t = Timings()
    t.add("retrieve", 10.0)
    t.add("retrieve", 5.0)
    assert t.as_dict()["retrieve"] == 15.0
    assert t.slowest() == ("retrieve", 15.0)


def test_report_warns_only_over_budget(caplog):
    t = Timings()
    t.add("retrieve", 50.0)
    with caplog.at_level(logging.WARNING, logger="moo.timing"):
        t.report("search mode=raw", 1_000_000.0)
    assert not caplog.records
    with caplog.at_level(logging.WARNING, logger="moo.timing"):
        t.report("search mode=raw", 0.0)
    assert "over budget" in caplog.text


def test_budget_for_known_modes():
    assert budget_for("raw") == BUDGET_MS["raw"] and budget_for("raw") <= 1000
    assert budget_for("nonsense") is None


def test_search_reports_stage_timings(patched):
    out = search_mod.search(_conn(), "postgres wal", mode="raw", live=False)
    timings = out["meta"]["timings_ms"]
    assert {"understand", "expand", "retrieve"} <= set(timings)
    assert all(ms >= 0 for ms in timings.values())
    assert out["meta"]["elapsed_ms"] >= 0


def test_search_reports_cost(patched):
    out = search_mod.search(_conn(), "postgres wal", mode="raw", live=False)
    cost = out["meta"]["cost"]
    assert cost["llm_calls"] == 0 and cost["llm_attempts"] == 0
    assert "by_stage" not in cost


def test_repeat_query_costs_nothing_once_a_stage_has_cached(monkeypatch):
    """With a provider configured, the second run of the same query reaches the
    cache before the provider: one call, then none."""
    from app import expand as expand_mod
    from app import llm

    conn = _conn()
    conn.execute("CREATE TABLE llm_cache (key TEXT PRIMARY KEY, value TEXT, model TEXT)")
    monkeypatch.setattr(llm, "_resolve_config", lambda: {
        "provider": "openai", "api_key": "k", "base_url": "http://x", "model": "m",
    })
    monkeypatch.setitem(llm._BACKENDS, "openai", lambda *a, **k: {"queries": ["postgres wal mode"]})

    llm.reset_stats()
    first = expand_mod.expand(conn, "how does postgres wal work")
    after_first = llm.get_stats()
    second = expand_mod.expand(conn, "how does postgres wal work")
    after_second = llm.get_stats()

    assert first == second == ["postgres wal mode"]
    assert after_first["calls"] == 1
    assert after_second["calls"] == 1
    assert after_second["cache_hits"] == 1


def test_cost_counts_attempts_when_stages_want_a_model(patched, monkeypatch):
    from app import llm

    monkeypatch.setattr(llm, "_resolve_config", lambda: None)
    out = search_mod.search(_conn(), "postgres vs mysql", mode="raw", live=False)
    before = out["meta"]["cost"]["llm_attempts"]
    assert before == 0

    calls = {"n": 0}

    def wants_a_model(query):
        llm.generate_json("expand this", schema={})
        calls["n"] += 1
        return []

    monkeypatch.setattr("app.expand.heuristic_expand", wants_a_model)
    out = search_mod.search(_conn(), "postgres vs mysql", mode="raw", live=False)
    cost = out["meta"]["cost"]
    assert calls["n"] == 1
    assert cost["llm_attempts"] == 1
    assert cost["llm_calls"] == 0
    assert cost["prompt_chars"] > 0
    assert cost["by_stage"]["expand"] == 1
