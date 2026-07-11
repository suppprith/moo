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

def test_server_smoke_lists_tools():
    """CI smoke check (SUP-118): the server object builds and exposes its tools,
    and the `moo-mcp` entry point is importable."""
    assert m.mcp.name == "moo-search"
    assert callable(m.main)
    tools = asyncio.run(m.mcp.list_tools())
    assert len(tools) == 5


def test_all_tools_registered_with_schemas():
    tools = asyncio.run(m.mcp.list_tools())
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"search", "fetch_source", "get_claim", "list_contradictions", "expand_graph"}
    for t in tools:
        assert t.description and len(t.description) > 40      # descriptions are load-bearing
        assert "properties" in (t.inputSchema or {})
    assert set(by_name["search"].inputSchema["properties"]) == {"query", "mode", "k", "fields"}


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
