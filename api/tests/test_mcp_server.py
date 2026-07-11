"""MCP server tools (app.mcp_server, SUP-115)."""

import asyncio

import pytest

from app import mcp_server as m


class _DummyConn:
    def close(self):
        pass


@pytest.fixture(autouse=True)
def _no_real_db(monkeypatch):
    monkeypatch.setattr(m, "_search_conn", lambda: _DummyConn())
    monkeypatch.setattr(m, "_plain_conn", lambda: _DummyConn())


# ---- registration + schemas -------------------------------------------------

_TOOLS = {"search", "fetch_source", "get_claim", "list_contradictions", "expand_graph",
          "deep_research", "research_status"}


def test_server_smoke_lists_tools():
    """CI smoke check (SUP-118): the server object builds and exposes its tools,
    and the `moo-mcp` entry point is importable."""
    assert m.mcp.name == "moo-search"
    assert callable(m.main)
    tools = asyncio.run(m.mcp.list_tools())
    assert len(tools) == len(_TOOLS)


def test_all_tools_registered_with_schemas():
    tools = asyncio.run(m.mcp.list_tools())
    by_name = {t.name: t for t in tools}
    assert set(by_name) == _TOOLS
    for t in tools:
        assert t.description and len(t.description) > 40      # descriptions are load-bearing
        assert "properties" in (t.inputSchema or {})
    assert set(by_name["search"].inputSchema["properties"]) == {"query", "mode", "k", "fields", "max_tokens"}


# ---- tool behaviour ---------------------------------------------------------

def test_search_tool_returns_agent_payload(monkeypatch):
    captured = {}

    def fake(conn, query, **kw):
        captured.update(kw)
        return {"sources": [{"id": "chk_1", "url": "u"}], "meta": {}}

    monkeypatch.setattr(m, "run_search", fake)
    out = m.search("postgres joins", mode="raw", k=3)
    assert out["sources"][0]["id"] == "chk_1"
    assert captured["format"] == "agent" and captured["k"] == 3


def test_search_tool_respects_max_tokens(monkeypatch):
    monkeypatch.setattr(m, "run_search", lambda *a, **k: {
        "sources": [{"id": f"chk_{i}", "pad": "x" * 400} for i in range(20)],
        "meta": {"page": {"next_cursor": "cur"}}})
    out = m.search("q", max_tokens=300)
    assert len(out["sources"]) < 20
    assert out["truncation"]["next_cursor"] == "cur"


def test_fetch_source_missing_raises(monkeypatch):
    monkeypatch.setattr(m.fetch_mod, "fetch_handle", lambda conn, h: None)
    with pytest.raises(ValueError):
        m.fetch_source("chk_999")


def test_fetch_source_returns_row(monkeypatch):
    monkeypatch.setattr(m.fetch_mod, "fetch_handle", lambda conn, h: {"id": h})
    assert m.fetch_source("doc_5")["id"] == "doc_5"


def test_get_claim_rejects_wrong_kind():
    with pytest.raises(ValueError):
        m.get_claim("chk_1")  # not a clm_ handle


def test_get_claim_returns_claim(monkeypatch):
    monkeypatch.setattr(m.fetch_mod, "fetch_claim", lambda conn, rowid: {"id": f"clm_{rowid}"})
    assert m.get_claim("clm_7")["id"] == "clm_7"


def test_list_contradictions_filters_to_disputed(monkeypatch):
    monkeypatch.setattr(m, "run_search", lambda *a, **k: {
        "intent": "comparison",
        "claims": [
            {"id": "clm_1", "disputed": True, "evidence": []},
            {"id": "clm_2", "disputed": False, "evidence": [{"relation": "supports"}]},
            {"id": "clm_3", "disputed": False, "evidence": [{"relation": "contradicts"}]},
        ],
        "meta": {},
    })
    out = m.list_contradictions("mongodb durability")
    got = {c["id"] for c in out["contradictions"]}
    assert got == {"clm_1", "clm_3"}  # disputed OR has a contradicting edge


def test_expand_graph_decodes_entity_handle(monkeypatch):
    seen = {}

    def fake_expand(conn, node, **kw):
        seen["node"] = node
        return {"center": {"id": 5}, "nodes": [], "edges": []}

    monkeypatch.setattr("app.graph.query.expand_node", fake_expand)
    m.expand_graph("ent_5")
    assert seen["node"] == "5"       # handle decoded to rowid string
    m.expand_graph("Postgres")
    assert seen["node"] == "Postgres"  # plain name passed through


def test_expand_graph_unknown_entity_raises(monkeypatch):
    monkeypatch.setattr("app.graph.query.expand_node", lambda conn, node, **kw: None)
    with pytest.raises(ValueError):
        m.expand_graph("Nonexistent")


# ---- deep_research + research_status (SUP-116) ------------------------------

def _stub_research(monkeypatch, *, exhausted=True):
    monkeypatch.setattr(m, "make_plan", lambda conn, q, **k: {
        "intent": "comparison", "entities": ["PostgreSQL"],
        "sub_questions": [{"id": 1, "question": "sq", "depends_on": []}]})
    monkeypatch.setattr(m.research_session, "create_run", lambda conn, q, plan: "r1")
    monkeypatch.setattr(m.research_session, "record_step", lambda *a, **k: None)
    monkeypatch.setattr(m.research_session, "finalize_run", lambda *a, **k: None)

    def fake_loop(conn, plan, *, on_step=None, **k):
        on_step({"step": 1, "sub_question_id": 1, "query": "sq", "reason": "plan",
                 "claims": 2, "new_claims": 2, "disputed": 0}, [(1, 1)])
        return {"coverage": [], "claims": [], "budget": {"exhausted": exhausted, "steps_used": 1}}

    monkeypatch.setattr(m, "run_loop", fake_loop)
    monkeypatch.setattr(m, "assemble_report", lambda conn, run, **k: {
        "executive_answer": "A [S1]", "findings": [], "disputed_points": [],
        "open_questions": [], "sources": []})


def test_deep_research_returns_report(monkeypatch):
    _stub_research(monkeypatch, exhausted=True)
    out = asyncio.run(m.deep_research("q", max_steps=1, ctx=None))
    assert out["run_id"] == "r1" and out["status"] == "partial" and out["partial"] is True
    assert out["executive_answer"] == "A [S1]"


def test_deep_research_reports_progress(monkeypatch):
    _stub_research(monkeypatch, exhausted=False)

    class FakeCtx:
        def __init__(self):
            self.progress, self.infos = [], []

        async def report_progress(self, progress, total=None, message=None):
            self.progress.append((progress, total, message))

        async def info(self, msg):
            self.infos.append(msg)

    ctx = FakeCtx()
    out = asyncio.run(m.deep_research("q", max_steps=1, ctx=ctx))
    assert out["status"] == "done"
    assert any("planned" in i for i in ctx.infos)
    assert ctx.progress and ctx.progress[0][0] == 1


def test_deep_research_shapes_to_budget_keeping_disputes(monkeypatch):
    _stub_research(monkeypatch, exhausted=False)
    monkeypatch.setattr(m, "assemble_report", lambda conn, run, **k: {
        "executive_answer": "A", "open_questions": [],
        "disputed_points": [{"text": "d" * 100} for _ in range(3)],
        "findings": [{"text": "f" * 400} for _ in range(30)],
        "sources": [{"handle": f"doc_{i}", "pad": "x" * 400} for i in range(30)]})
    out = asyncio.run(m.deep_research("q", max_tokens=500, ctx=None))
    assert len(out["disputed_points"]) == 3          # contradictions never dropped
    assert "truncation" in out


def test_deep_research_error_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("planner blew up")

    monkeypatch.setattr(m, "make_plan", boom)
    with pytest.raises(ValueError):
        asyncio.run(m.deep_research("q", ctx=None))


def test_research_status_returns_report(monkeypatch):
    monkeypatch.setattr(m.research_session, "get_run", lambda conn, rid: {"run_id": rid, "status": "done"})
    monkeypatch.setattr(m, "assemble_report", lambda conn, run, **k: {"findings": []})
    out = m.research_status("r1")
    assert out["run_id"] == "r1" and out["status"] == "done" and out["partial"] is False


def test_research_status_unknown_raises(monkeypatch):
    monkeypatch.setattr(m.research_session, "get_run", lambda conn, rid: None)
    with pytest.raises(ValueError):
        m.research_status("nope")
