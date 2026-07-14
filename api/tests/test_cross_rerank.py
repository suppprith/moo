"""Cross-encoder rerank backend (app.rerank, SUP-139). Model is stubbed —
these test the wiring/guarantees; ranking quality is SUP-141's benchmark."""

import sqlite3
from dataclasses import dataclass, field

import pytest

from app import rerank as rerank_mod


@dataclass
class Hit:
    chunk_id: int
    text: str
    score: float = 0.5
    rank_signals: dict | None = None
    alternates: list = field(default_factory=list)


class StubCross:
    """Scores by presence of the word 'vacuum' — deterministic, no download."""

    def predict(self, pairs):
        return [1.0 if "vacuum" in text.lower() else 0.1 for _, text in pairs]


class BoomCross:
    def predict(self, pairs):
        raise RuntimeError("model exploded")


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE llm_cache (key TEXT PRIMARY KEY, stage TEXT, value TEXT, "
              "model TEXT, created_at TEXT DEFAULT (datetime('now')))")
    return c


HITS = [
    Hit(1, "Redis persistence uses RDB snapshots.", rank_signals={"fused": 0.6}),
    Hit(2, "Vacuum reclaims dead tuples in Postgres.", rank_signals={"fused": 0.5}),
    Hit(3, "Indexes speed up reads.", rank_signals={"fused": 0.4}),
]


def _hits():
    return [Hit(h.chunk_id, h.text, h.score, dict(h.rank_signals)) for h in HITS]


def test_cross_backend_reorders(conn, monkeypatch):
    monkeypatch.setenv("MOO_RERANKER", "cross")
    monkeypatch.setattr(rerank_mod, "_get_cross_model", lambda: StubCross())
    out = rerank_mod.rerank(conn, "postgres vacuum", _hits())
    assert out[0].chunk_id == 2                       # the on-topic hit wins
    assert out[0].rank_signals["cross"] == 1.0        # score surfaced
    assert {h.chunk_id for h in out} == {1, 2, 3}     # nothing dropped


def test_cross_failure_keeps_fused_order(conn, monkeypatch):
    monkeypatch.setenv("MOO_RERANKER", "cross")
    monkeypatch.setattr(rerank_mod, "_get_cross_model", lambda: BoomCross())
    hits = _hits()
    out = rerank_mod.rerank(conn, "postgres vacuum", hits)
    assert [h.chunk_id for h in out] == [1, 2, 3]     # identity, never breaks


def test_off_backend_is_identity(conn, monkeypatch):
    monkeypatch.setenv("MOO_RERANKER", "off")
    out = rerank_mod.rerank(conn, "postgres vacuum", _hits())
    assert [h.chunk_id for h in out] == [1, 2, 3]


def test_default_backend_is_llm(conn, monkeypatch):
    monkeypatch.delenv("MOO_RERANKER", raising=False)
    called = {"llm": False}

    def fake_llm_order(query, candidates):
        called["llm"] = True
        return None  # keyless: identity fallback

    monkeypatch.setattr(rerank_mod, "_llm_order", fake_llm_order)
    out = rerank_mod.rerank(conn, "postgres vacuum", _hits())
    assert called["llm"] is True
    assert [h.chunk_id for h in out] == [1, 2, 3]


def test_only_top_n_reordered(conn, monkeypatch):
    monkeypatch.setenv("MOO_RERANKER", "cross")
    monkeypatch.setattr(rerank_mod, "_get_cross_model", lambda: StubCross())
    many = _hits() + [Hit(i, f"filler text {i}") for i in range(10, 40)]
    out = rerank_mod.cross_rerank("postgres vacuum", many, top_n=3)
    assert out[0].chunk_id == 2
    assert [h.chunk_id for h in out[3:]] == [i for i in range(10, 40)]  # tail untouched


def test_single_hit_short_circuits(conn, monkeypatch):
    monkeypatch.setenv("MOO_RERANKER", "cross")
    one = [HITS[0]]
    assert rerank_mod.rerank(conn, "q", one) is one
