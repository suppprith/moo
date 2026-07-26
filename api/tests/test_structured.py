"""Caller-defined output schemas on deep_research."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import mcp_server as m
from app.main import app
from app.research import structured as st
from app.research.structured import structure_report, validate_schema

client = TestClient(app, raise_server_exceptions=False)


REPORT = {
    "question": "How does Postgres autovacuum work?",
    "executive_answer": "Autovacuum automates VACUUM and ANALYZE [S1].",
    "findings": [
        {"claim": "clm_1", "confidence": 0.9, "disputed": False,
         "text": "Autovacuum automates the execution of VACUUM and ANALYZE commands [S1]."},
        {"claim": "clm_2", "confidence": 0.7, "disputed": False,
         "text": "Autovacuum triggers when dead tuples exceed a threshold computed from "
                 "autovacuum_vacuum_scale_factor [S2]."},
    ],
    "disputed_points": [],
    "open_questions": [],
    "sources": [],
}

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "one-line answer"},
        "trigger_condition": {"type": "string", "description": "when autovacuum triggers dead tuples threshold"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "rows_per_second": {"type": "number"},
    },
}


def test_rejects_non_object_schema():
    with pytest.raises(ValueError):
        validate_schema({"type": "array"})
    with pytest.raises(ValueError):
        validate_schema({"type": "object", "properties": {}})
    with pytest.raises(ValueError):
        validate_schema("not a schema")


def test_rejects_unsupported_type_and_depth():
    with pytest.raises(ValueError):
        validate_schema({"type": "object", "properties": {"f": {"type": "date"}}})
    deep = {"type": "string"}
    for _ in range(6):
        deep = {"type": "object", "properties": {"n": deep}}
    with pytest.raises(ValueError):
        validate_schema(deep)


def test_rejects_too_many_fields():
    props = {f"f{i}": {"type": "string"} for i in range(st.MAX_PROPERTIES + 1)}
    with pytest.raises(ValueError):
        validate_schema({"type": "object", "properties": props})


def test_heuristic_fill_populates_from_findings():
    out = structure_report(None, REPORT, SCHEMA, use_llm=False)
    assert out["generator"] == "heuristic"
    s = out["output"]
    assert s["summary"] == REPORT["executive_answer"]
    assert "scale_factor" in s["trigger_condition"]
    assert s["key_points"] and all(isinstance(p, str) for p in s["key_points"])
    assert s["rows_per_second"] is None
    assert out["grounding"]["$.trigger_condition"] == ["clm_2"]
    assert not out["ungrounded_fields"]


def test_llm_fill_keeps_grounded_fields(monkeypatch):
    monkeypatch.setattr(st.llm, "cached_json", lambda *a, **k: {
        "summary": "Autovacuum automates VACUUM and ANALYZE commands [S1].",
        "trigger_condition": "Dead tuples exceed a threshold from autovacuum_vacuum_scale_factor.",
        "key_points": [],
        "rows_per_second": None,
    })
    out = structure_report(None, REPORT, SCHEMA, use_llm=True)
    assert out["generator"] == "model"
    assert out["output"]["summary"].startswith("Autovacuum automates")
    assert "$.summary" in out["grounding"]
    assert not out["ungrounded_fields"]


def test_llm_fabrication_is_nulled(monkeypatch):
    monkeypatch.setattr(st.llm, "cached_json", lambda *a, **k: {
        "summary": "Sharks must swim continuously or they sink to the ocean floor.",
        "trigger_condition": None,
        "key_points": [],
        "rows_per_second": 12345,
    })
    out = structure_report(None, REPORT, SCHEMA, use_llm=True)
    assert out["output"]["summary"] is None
    assert out["output"]["rows_per_second"] is None
    assert "$.summary" in out["ungrounded_fields"]
    assert "$.rows_per_second" in out["ungrounded_fields"]
    assert "not fabricated" in out["note"]


def test_llm_failure_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr(st.llm, "cached_json", lambda *a, **k: None)
    out = structure_report(None, REPORT, SCHEMA, use_llm=True)
    assert out["generator"] == "heuristic"
    assert out["output"]["summary"]


def test_research_endpoint_rejects_bad_schema():
    r = client.post("/research", json={"question": "q", "output_schema": {"type": "array"}})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_research_stream_rejects_bad_schema():
    r = client.post("/research/stream", json={"question": "q", "output_schema": {"type": "array"}})
    assert r.status_code == 422


class _DummyConn:
    def close(self):
        pass


def _stub_research(monkeypatch):
    monkeypatch.setattr(m, "_search_conn", lambda: _DummyConn())
    monkeypatch.setattr(m, "make_plan", lambda conn, q, **k: {
        "intent": "definition", "entities": [], "sub_questions": []})
    monkeypatch.setattr(m.research_session, "create_run", lambda conn, q, plan: "r1")
    monkeypatch.setattr(m.research_session, "finalize_run", lambda *a, **k: None)
    monkeypatch.setattr(m, "run_loop", lambda conn, plan, **k: {
        "coverage": [], "claims": [], "budget": {"exhausted": False}})
    monkeypatch.setattr(m, "assemble_report", lambda conn, run, **k: dict(REPORT))
    monkeypatch.setattr(st.llm, "cached_json", lambda *a, **k: None)


def test_mcp_deep_research_structured(monkeypatch):
    _stub_research(monkeypatch)
    out = asyncio.run(m.deep_research("q", output_schema=SCHEMA, ctx=None))
    assert out["structured"]["output"]["summary"]
    assert out["structured"]["grounding"]


def test_mcp_deep_research_bad_schema_fails_fast(monkeypatch):
    _stub_research(monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(m.deep_research("q", output_schema={"type": "array"}, ctx=None))
