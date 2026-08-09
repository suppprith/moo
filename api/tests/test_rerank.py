"""Unit tests for listwise reranking (app.rerank)."""

import sqlite3
from dataclasses import dataclass

from app import rerank


@dataclass
class FakeHit:
    chunk_id: int
    text: str = "some text"
    source_type: str = "docs"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE llm_cache (key TEXT PRIMARY KEY, value TEXT, model TEXT)")
    return conn


def _hits(*ids) -> list[FakeHit]:
    return [FakeHit(i) for i in ids]


def test_apply_reorders_drops_and_keeps_tail():
    hits = _hits(1, 2, 3, 4)
    out = rerank._apply(hits, order=[3, 1], irrelevant={2})
    assert [h.chunk_id for h in out] == [3, 1, 4]


def test_fallback_is_identity_without_llm():
    conn = _db()
    hits = _hits(1, 2, 3)
    out = rerank.rerank(conn, "q", hits, use_llm=False)
    assert [h.chunk_id for h in out] == [1, 2, 3]


def test_trivial_candidate_set_short_circuits():
    conn = _db()
    assert rerank.rerank(conn, "q", _hits(1)) == rerank.rerank(conn, "q", _hits(1))


def test_uses_cache_and_skips_llm_on_hit(monkeypatch):
    conn = _db()
    hits = _hits(1, 2, 3)
    key = rerank._cache_key("q", [1, 2, 3])
    from app import llm
    llm.cache_put(conn, key, {"order": [3, 2, 1], "irrelevant": []}, "test")

    calls = {"n": 0}
    def boom(*a, **k):
        calls["n"] += 1
        return None
    monkeypatch.setattr(rerank, "_llm_order", boom)

    out = rerank.rerank(conn, "q", hits, use_llm=True)
    assert [h.chunk_id for h in out] == [3, 2, 1]
    assert calls["n"] == 0


def test_budget_caps_candidates_sent(monkeypatch):
    conn = _db()
    hits = _hits(*range(1, 31))
    captured = {}
    def fake_order(query, candidates):
        captured["n"] = len(candidates)
        return {"order": [c for c, _ in candidates], "irrelevant": []}
    monkeypatch.setattr(rerank, "_llm_order", fake_order)

    rerank.rerank(conn, "q", hits, use_llm=True)
    assert captured["n"] == rerank.MAX_CANDIDATES


def test_unusable_response_is_cached_so_a_repeat_costs_nothing(monkeypatch):
    from app import llm

    conn = _db()
    calls = {"n": 0}

    def unusable(*a, **k):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(llm, "get_client", lambda: {"provider": "openai"})
    monkeypatch.setattr(llm, "generate_json", unusable)

    first = rerank.rerank(conn, "q", _hits(1, 2, 3))
    second = rerank.rerank(conn, "q", _hits(1, 2, 3))
    assert [h.chunk_id for h in first] == [1, 2, 3]
    assert [h.chunk_id for h in second] == [1, 2, 3]
    assert calls["n"] == 1


def test_unusable_response_is_not_cached_without_a_provider(monkeypatch):
    from app import llm

    conn = _db()
    monkeypatch.setattr(llm, "get_client", lambda: None)
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: None)
    rerank.rerank(conn, "q", _hits(1, 2, 3))
    assert conn.execute("SELECT count(*) FROM llm_cache").fetchone()[0] == 0
