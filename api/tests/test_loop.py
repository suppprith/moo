"""Iterative research loop."""

import sqlite3

import pytest

from app.research import loop as loop_mod


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE claim (id INTEGER PRIMARY KEY, confidence REAL, disputed INTEGER)")
    return c


def _plan(*questions):
    return {
        "question": "root question",
        "intent": "comparison",
        "sub_questions": [{"id": i, "question": q, "depends_on": []} for i, q in enumerate(questions, 1)],
    }


@pytest.fixture(autouse=True)
def _stub_stages(monkeypatch):
    """No-op link; score returns high-confidence non-disputed by default. Tests
    override extract/score as needed."""
    monkeypatch.setattr(loop_mod, "link_claim", lambda *a, **k: {})
    monkeypatch.setattr(loop_mod, "score_claim", lambda conn, cid: {"confidence": 0.8, "disputed": False})
    monkeypatch.setattr(loop_mod, "expand", lambda conn, q, **k: [f"{q} reformulated"])


def _extract_map(mapping, default=None):
    """Build an extract_claims stub keyed by exact query string."""
    def fake(conn, query, *, k=8, use_llm=True):
        return mapping.get(query, default if default is not None else [])
    return fake


def test_runs_each_sub_question_once_when_well_covered(conn, monkeypatch):
    claims = [{"id": 1, "text": "c1", "chunk_ids": [10]}, {"id": 2, "text": "c2", "chunk_ids": [11]}]
    monkeypatch.setattr(loop_mod, "extract_claims", lambda *a, **k: claims)
    out = loop_mod.run_loop(conn, _plan("q one", "q two"), use_llm=False)
    assert [s["reason"] for s in out["steps"]] == ["plan", "plan"]
    assert {c["id"] for c in out["claims"]} == {1, 2}
    assert all(cov["covered"] for cov in out["coverage"])
    assert out["budget"]["exhausted"] is False


def test_thin_coverage_spawns_one_gap_followup(conn, monkeypatch):
    mapping = {
        "thin q": [{"id": 1, "text": "only", "chunk_ids": [1]}],
        "thin q reformulated": [{"id": 2, "text": "more", "chunk_ids": [2]}],
    }
    monkeypatch.setattr(loop_mod, "extract_claims", _extract_map(mapping))
    out = loop_mod.run_loop(conn, _plan("thin q"), use_llm=False)
    reasons = [(s["reason"], s["query"]) for s in out["steps"]]
    assert reasons == [("plan", "thin q"), ("gap", "thin q reformulated")]
    assert {c["id"] for c in out["claims"]} == {1, 2}


def test_disputed_claim_triggers_contradiction_chase(conn, monkeypatch):
    monkeypatch.setattr(loop_mod, "extract_claims",
                        _extract_map({"cmp": [{"id": 5, "text": "A beats B", "chunk_ids": [1]}]}, default=[]))
    monkeypatch.setattr(loop_mod, "score_claim", lambda conn, cid: {"confidence": 0.5, "disputed": True})
    out = loop_mod.run_loop(conn, _plan("cmp"), use_llm=False)
    kinds = [s["reason"] for s in out["steps"]]
    assert "contradiction" in kinds
    chase = next(s for s in out["steps"] if s["reason"] == "contradiction")
    assert chase["query"] == "A beats B"
    assert out["disputed_claim_ids"] == [5]


def test_hard_step_budget_stops_and_marks_exhausted(conn, monkeypatch):
    monkeypatch.setattr(loop_mod, "extract_claims",
                        lambda conn, q, **k: [{"id": hash(q) % 1000, "text": q, "chunk_ids": [1]}])
    out = loop_mod.run_loop(conn, _plan("a", "b", "c", "d"), max_steps=2, use_llm=False)
    assert out["budget"]["steps_used"] == 2
    assert out["budget"]["exhausted"] is True
    assert any(not cov["covered"] for cov in out["coverage"])


def test_same_query_not_run_twice(conn, monkeypatch):
    calls = []
    def fake(conn, query, *, k=8, use_llm=True):
        calls.append(query)
        return [{"id": 1, "text": "c", "chunk_ids": [1]}, {"id": 2, "text": "d", "chunk_ids": [2]}]
    monkeypatch.setattr(loop_mod, "extract_claims", fake)
    out = loop_mod.run_loop(conn, _plan("dup", "dup"), use_llm=False)
    assert calls == ["dup"]
    assert out["budget"]["steps_used"] == 1


def test_too_similar_suppresses_near_dup_only():
    seen = {"how do you fix connection pool exhaustion"}
    assert loop_mod._too_similar("how do you fix connection pool exhaustion", seen)
    assert not loop_mod._too_similar("redis persistence tradeoffs", seen)


def test_claim_linked_and_scored_once_across_sub_questions(conn, monkeypatch):
    monkeypatch.setattr(loop_mod, "extract_claims", lambda *a, **k: [{"id": 9, "text": "shared", "chunk_ids": [1]}])
    scored = []
    monkeypatch.setattr(loop_mod, "score_claim",
                        lambda conn, cid: scored.append(cid) or {"confidence": 0.7, "disputed": False})
    loop_mod.run_loop(conn, _plan("qa", "qb"), use_llm=False)
    assert scored == [9]
