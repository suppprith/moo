"""Research-run persistence (app.research.session, SUP-112)."""

import sqlite3

import pytest

from app.research import session as sess


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(
        """
        CREATE TABLE claim (id INTEGER PRIMARY KEY, text TEXT, confidence REAL, disputed INTEGER);
        CREATE TABLE claim_chunk (claim_id INTEGER, chunk_id INTEGER);
        CREATE TABLE research_run (
            id TEXT PRIMARY KEY, question TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running',
            intent TEXT, plan TEXT, coverage TEXT, budget TEXT, generator TEXT, error TEXT,
            created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE research_step (
            id INTEGER PRIMARY KEY, run_id TEXT REFERENCES research_run(id) ON DELETE CASCADE,
            step_no INTEGER, sub_question_id INTEGER, query TEXT, reason TEXT,
            claims INTEGER, new_claims INTEGER, disputed INTEGER,
            created_at TEXT DEFAULT (datetime('now')), UNIQUE (run_id, step_no)
        );
        CREATE TABLE research_claim (
            run_id TEXT REFERENCES research_run(id) ON DELETE CASCADE,
            claim_id INTEGER REFERENCES claim(id) ON DELETE CASCADE,
            sub_question_id INTEGER, PRIMARY KEY (run_id, claim_id)
        );
        """
    )
    c.execute("INSERT INTO claim VALUES (1, 'PG handles joins well', 0.8, 0)")
    c.execute("INSERT INTO claim VALUES (2, 'MySQL disputed point', 0.5, 1)")
    c.execute("INSERT INTO claim_chunk VALUES (1, 10)")
    c.execute("INSERT INTO claim_chunk VALUES (2, 11)")
    c.commit()
    return c


_PLAN = {"question": "q", "intent": "comparison", "generator": "heuristic",
         "sub_questions": [{"id": 1, "question": "sq1", "depends_on": []}]}


def test_create_and_get_run(conn):
    rid = sess.create_run(conn, "q", _PLAN)
    sess.record_step(conn, rid, {"step": 1, "sub_question_id": 1, "query": "sq1", "reason": "plan",
                                 "claims": 2, "new_claims": 2, "disputed": 1}, [(1, 1), (2, 1)])
    sess.finalize_run(conn, rid, {"coverage": [{"sub_question_id": 1, "covered": True}],
                                  "budget": {"exhausted": False, "steps_used": 1}}, "done")
    run = sess.get_run(conn, rid)
    assert run["status"] == "done" and run["question"] == "q"
    assert [s["query"] for s in run["steps"]] == ["sq1"]
    assert {c["id"] for c in run["claims"]} == {1, 2}
    assert run["disputed_claim_ids"] == [2]
    assert run["source_chunk_ids"] == [10, 11]
    assert run["plan"]["intent"] == "comparison"


def test_get_run_unknown_is_none(conn):
    assert sess.get_run(conn, "nope") is None


def test_record_step_is_idempotent(conn):
    rid = sess.create_run(conn, "q", _PLAN)
    step = {"step": 1, "sub_question_id": 1, "query": "sq1", "reason": "plan",
            "claims": 1, "new_claims": 1, "disputed": 0}
    sess.record_step(conn, rid, step, [(1, 1)])
    sess.record_step(conn, rid, step, [(1, 1)])  # replay -> no duplicate rows
    run = sess.get_run(conn, rid)
    assert len(run["steps"]) == 1 and len(run["claims"]) == 1


def test_delete_run_purges_but_keeps_claims(conn):
    rid = sess.create_run(conn, "q", _PLAN)
    sess.record_step(conn, rid, {"step": 1, "sub_question_id": 1, "query": "sq1", "reason": "plan",
                                 "claims": 1, "new_claims": 1, "disputed": 0}, [(1, 1)])
    assert sess.delete_run(conn, rid) is True
    assert sess.get_run(conn, rid) is None
    assert conn.execute("SELECT COUNT(*) FROM research_step").fetchone()[0] == 0
    # the shared claim survives the purge
    assert conn.execute("SELECT COUNT(*) FROM claim").fetchone()[0] == 2
    assert sess.delete_run(conn, rid) is False  # already gone


def test_list_runs(conn):
    a = sess.create_run(conn, "first", _PLAN)
    b = sess.create_run(conn, "second", _PLAN)
    runs = {r["run_id"] for r in sess.list_runs(conn)}
    assert {a, b} <= runs


# ---- orchestrator ----------------------------------------------------------

def test_run_research_persists_and_finalizes(conn, monkeypatch):
    monkeypatch.setattr(sess, "make_plan", lambda conn, q, **k: _PLAN)

    def fake_loop(conn, plan, *, on_step=None, **k):
        on_step({"step": 1, "sub_question_id": 1, "query": "sq1", "reason": "plan",
                 "claims": 2, "new_claims": 2, "disputed": 1}, [(1, 1), (2, 1)])
        return {"coverage": [{"sub_question_id": 1, "covered": True}], "claims": [],
                "budget": {"exhausted": True, "steps_used": 1}}

    monkeypatch.setattr(sess, "run_loop", fake_loop)
    out = sess.run_research(conn, "q", use_llm=False)
    assert out["status"] == "partial"          # exhausted -> partial
    assert "cost" in out and out["cost"]["llm_calls"] == 0   # faked/keyless: no LLM calls
    persisted = sess.get_run(conn, out["run_id"])
    assert persisted["status"] == "partial"
    assert len(persisted["steps"]) == 1
    assert {c["id"] for c in persisted["claims"]} == {1, 2}


def test_run_research_marks_failed_on_error(conn, monkeypatch):
    monkeypatch.setattr(sess, "make_plan", lambda conn, q, **k: _PLAN)

    def boom(conn, plan, *, on_step=None, **k):
        raise RuntimeError("retrieval blew up")

    monkeypatch.setattr(sess, "run_loop", boom)
    with pytest.raises(RuntimeError):
        sess.run_research(conn, "q", use_llm=False)
    # the run was created and marked failed before the error propagated
    runs = sess.list_runs(conn)
    assert runs and runs[0]["status"] == "failed"
    assert sess.get_run(conn, runs[0]["run_id"])["error"] == "retrieval blew up"
