"""LLM cache helper + cost stats."""

import sqlite3

from app import llm


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE llm_cache (key TEXT PRIMARY KEY, value TEXT, model TEXT)")
    return c


def test_cached_json_second_call_is_a_cache_hit(monkeypatch):
    conn = _conn()
    calls = {"n": 0}

    def fake(prompt, *, schema, **kw):
        calls["n"] += 1
        return {"ok": calls["n"]}

    monkeypatch.setattr(llm, "generate_json", fake)
    llm.reset_stats()
    a = llm.cached_json(conn, "claims", "key1", "prompt", schema={})
    b = llm.cached_json(conn, "claims", "key1", "prompt", schema={})
    assert a == b == {"ok": 1}
    assert calls["n"] == 1
    s = llm.get_stats()
    assert s["cache_hits"] == 1 and s["cache_misses"] == 1


def test_cached_json_does_not_cache_none(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: None)
    llm.reset_stats()
    assert llm.cached_json(conn, "claims", "k", "p", schema={}) is None
    assert llm.cached_json(conn, "claims", "k", "p", schema={}) is None
    assert llm.get_stats()["cache_misses"] == 2


def test_reset_and_get_stats():
    conn = _conn()
    llm.cache_get(conn, "missing")
    llm.reset_stats()
    assert llm.get_stats() == {"calls": 0, "cache_hits": 0, "cache_misses": 0}
