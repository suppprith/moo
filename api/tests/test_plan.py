"""Research planner."""

import sqlite3

import pytest

from app.research import plan as plan_mod


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE llm_cache (key TEXT PRIMARY KEY, value TEXT, model TEXT)")
    return c


def test_comparison_plan_splits_by_axes_with_final_choice(conn):
    p = plan_mod.plan(conn, "Postgres vs MySQL for complex joins", use_llm=False)
    assert p["intent"] == "comparison"
    assert p["entities"][:2] == ["PostgreSQL", "MySQL"]
    qs = [s["question"] for s in p["sub_questions"]]
    assert any("compare on performance" in q for q in qs)
    assert qs[-1].startswith("When should you choose")
    assert p["sub_questions"][-1]["depends_on"] == [s["id"] for s in p["sub_questions"][:-1]]


def test_troubleshooting_plan_is_cause_diagnose_fix_prevent(conn):
    p = plan_mod.plan(conn, "postgres connection pool exhausted error", use_llm=False)
    assert p["intent"] == "troubleshooting"
    joined = " ".join(s["question"].lower() for s in p["sub_questions"])
    for kw in ("causes", "diagnose", "fix", "prevent"):
        assert kw in joined


def test_plan_is_bounded_and_ordered(conn):
    p = plan_mod.plan(conn, "compare redis and postgres and mysql and sqlite everything", use_llm=False)
    assert 1 <= len(p["sub_questions"]) <= plan_mod.MAX_SUB_QUESTIONS
    assert [s["id"] for s in p["sub_questions"]] == list(range(1, len(p["sub_questions"]) + 1))


def test_evidence_targets_present(conn):
    p = plan_mod.plan(conn, "what is a write-ahead log", use_llm=False)
    assert p["evidence_targets"]


def test_plan_is_cached_by_normalized_question(conn):
    p1 = plan_mod.plan(conn, "Postgres  vs   MySQL", use_llm=False)
    from app import llm
    p2 = plan_mod.plan(conn, "postgres vs mysql", use_llm=True)
    assert p2 == p1
    assert llm.cache_get(conn, llm.cache_key("plan", "postgres vs mysql")) == p1


def test_llm_plan_normalizes_ids_and_deps(conn, monkeypatch):
    payload = {
        "sub_questions": [
            {"question": "What is MVCC?", "depends_on": []},
            {"question": "How does VACUUM reclaim rows?", "depends_on": [1]},
            {"question": "Final synthesis", "depends_on": [1, 2, 9]},
        ],
        "comparison_axes": [],
        "evidence_targets": ["docs", "github_issue"],
    }
    monkeypatch.setattr(plan_mod.llm, "generate_json", lambda *a, **k: payload)
    p = plan_mod.plan(conn, "how does postgres mvcc work", use_llm=True)
    assert p["generator"] == plan_mod.llm.CHEAP_MODEL
    assert [s["id"] for s in p["sub_questions"]] == [1, 2, 3]
    assert p["sub_questions"][1]["depends_on"] == [1]
    assert p["sub_questions"][2]["depends_on"] == [1, 2]
    assert p["evidence_targets"] == ["docs", "github_issue"]


def test_llm_failure_falls_back_to_heuristic(conn, monkeypatch):
    monkeypatch.setattr(plan_mod.llm, "generate_json", lambda *a, **k: None)
    p = plan_mod.plan(conn, "Postgres vs MySQL", use_llm=True)
    assert p["generator"] == "heuristic"
    assert p["sub_questions"]


def test_llm_bounded_to_max(conn, monkeypatch):
    payload = {"sub_questions": [{"question": f"q{i}"} for i in range(20)]}
    monkeypatch.setattr(plan_mod.llm, "generate_json", lambda *a, **k: payload)
    p = plan_mod.plan(conn, "a broad question about databases", use_llm=True)
    assert len(p["sub_questions"]) == plan_mod.MAX_SUB_QUESTIONS
