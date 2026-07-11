"""Deep-research HTTP endpoints (SUP-114)."""

import json

from fastapi.testclient import TestClient

from app import main


class _DummyConn:
    def close(self):
        pass


_REPORT = {
    "question": "q", "executive_answer": "Postgres wins on joins [S1]",
    "findings": [{"claim": "clm_1", "text": "x", "confidence": 0.8, "citations": [1]}],
    "disputed_points": [], "open_questions": ["what about scale?"],
    "sources": [{"index": 1, "handle": "doc_1", "trust_tier": "high"}], "generator": "template",
}


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        events.append((event, data))
    return events


def test_post_research_returns_report(monkeypatch):
    monkeypatch.setattr(main, "get_connection_for_search", lambda: _DummyConn())
    monkeypatch.setattr(main.research_session, "run_research",
                        lambda conn, q, **k: {"run_id": "r1", "status": "partial", "steps": [{"step": 1}],
                                              "coverage": [], "budget": {"exhausted": True}, "claims": []})
    monkeypatch.setattr(main, "assemble_report", lambda conn, run, **k: dict(_REPORT))
    client = TestClient(main.app)
    r = client.post("/research", json={"question": "postgres vs mysql", "max_steps": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == "r1" and body["status"] == "partial"
    assert body["executive_answer"].startswith("Postgres")
    assert body["open_questions"] == ["what about scale?"]


def test_get_research_run_not_found(monkeypatch):
    monkeypatch.setattr(main, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(main.research_session, "get_run", lambda conn, rid: None)
    client = TestClient(main.app)
    r = client.get("/research/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_get_research_run_returns_report(monkeypatch):
    monkeypatch.setattr(main, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(main.research_session, "get_run",
                        lambda conn, rid: {"run_id": rid, "status": "done", "steps": [], "coverage": [], "budget": {}})
    monkeypatch.setattr(main, "assemble_report", lambda conn, run, **k: dict(_REPORT))
    client = TestClient(main.app)
    r = client.get("/research/r1")
    assert r.status_code == 200 and r.json()["run_id"] == "r1"


def test_research_stream_emits_plan_progress_report_done(monkeypatch):
    monkeypatch.setattr(main, "get_connection_for_search", lambda: _DummyConn())
    monkeypatch.setattr(main, "make_plan",
                        lambda conn, q, **k: {"intent": "comparison", "entities": ["PostgreSQL"],
                                              "sub_questions": [{"id": 1, "question": "sq1", "depends_on": []}]})
    monkeypatch.setattr(main.research_session, "create_run", lambda conn, q, plan: "r1")
    monkeypatch.setattr(main.research_session, "record_step", lambda *a, **k: None)
    monkeypatch.setattr(main.research_session, "finalize_run", lambda *a, **k: None)

    def fake_loop(conn, plan, *, on_step=None, **k):
        on_step({"step": 1, "sub_question_id": 1, "query": "sq1", "reason": "plan",
                 "claims": 2, "new_claims": 2, "disputed": 0}, [(1, 1)])
        return {"coverage": [], "claims": [], "budget": {"exhausted": False, "steps_used": 1}}

    monkeypatch.setattr(main, "run_loop", fake_loop)
    monkeypatch.setattr(main, "assemble_report", lambda conn, run, **k: dict(_REPORT))
    client = TestClient(main.app)
    r = client.post("/research/stream", json={"question": "postgres vs mysql"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    kinds = [e for e, _ in parse_sse(r.text)]
    assert kinds == ["plan", "run", "progress", "report", "done"]


def test_research_stream_reports_error_in_band(monkeypatch):
    monkeypatch.setattr(main, "get_connection_for_search", lambda: _DummyConn())
    monkeypatch.setattr(main, "make_plan", lambda conn, q, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    client = TestClient(main.app)
    r = client.post("/research/stream", json={"question": "x"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    assert events[-1][0] == "error" and events[-1][1]["error"]["code"] == "internal"
