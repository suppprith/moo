"""Unit tests for the unified search pipeline (app.search)."""

import sqlite3
from dataclasses import dataclass, field

import pytest

from app import search as search_mod


@dataclass
class FakeHit:
    chunk_id: int
    score: float = 0.5
    text: str = "text"
    heading: str | None = "H"
    url_anchor: str = "#a"
    source_type: str = "docs"
    document_url: str = "http://d"
    title: str | None = "T"
    published_at: str | None = None
    author_role: str | None = None
    popularity: int | None = None
    trust_score: float | None = 0.9
    alternates: list = field(default_factory=list)


@pytest.fixture
def patched(monkeypatch):
    """Stub retrieval so search runs without indexes; count real LLM calls."""
    monkeypatch.setattr(search_mod, "retrieve", lambda *a, **k: [FakeHit(1), FakeHit(2)])
    from app import llm

    calls = {"n": 0}
    orig = llm.generate_json

    def counting(*a, **k):
        calls["n"] += 1
        return None

    monkeypatch.setattr(llm, "generate_json", counting)
    return calls


def test_raw_mode_makes_zero_llm_calls(patched):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    out = search_mod.search(conn, "Postgres vs MySQL", mode="raw", use_llm=True)
    assert patched["n"] == 0                      # the whole point of raw
    assert out["answer"] is None and out["claims"] == []
    assert len(out["sources"]) == 2
    assert out["meta"]["contract_version"] == search_mod.CONTRACT_VERSION
    assert "elapsed_ms" in out["meta"]


def test_contract_has_all_required_keys(patched):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    out = search_mod.search(conn, "q", mode="raw")
    for key in ("query", "mode", "intent", "answer", "claims", "graph", "sources", "citations", "meta"):
        assert key in out


def test_invalid_mode_rejected():
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError):
        search_mod.search(conn, "q", mode="bogus")


def test_invalid_format_rejected(patched):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with pytest.raises(ValueError):
        search_mod.search(conn, "q", format="xml")


def test_invalid_fields_rejected(patched):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with pytest.raises(ValueError):
        search_mod.search(conn, "q", fields="sources,bogus")


# ---- agent shaping (SUP-105) ------------------------------------------------

def test_agent_format_is_compact_and_handle_addressed(patched):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    out = search_mod.search(conn, "q", mode="raw", format="agent")
    # opaque handles, not bare rowids
    assert out["sources"][0]["id"].startswith("chk_")
    assert "chunk_id" not in out["sources"][0]
    assert out["sources"][0]["url"] and "score" in out["sources"][0]
    # empty/null sections dropped in agent format
    assert "answer" not in out and "claims" not in out
    # always-present keys survive
    for key in ("query", "mode", "intent", "meta"):
        assert key in out


def test_fields_selection_drops_unselected_sections(patched):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    out = search_mod.search(conn, "q", mode="raw", fields="sources")
    assert "sources" in out
    for dropped in ("claims", "graph", "answer", "citations"):
        assert dropped not in out
    # meta + echo always kept
    assert out["query"] == "q" and "meta" in out


def test_full_format_no_fields_is_unchanged_legacy_shape(patched):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    out = search_mod.search(conn, "q", mode="raw")
    for key in ("query", "mode", "intent", "answer", "claims", "graph", "sources", "citations", "meta"):
        assert key in out


def test_pagination_meta_and_has_more(patched):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # stub always yields 2 hits; k=1 -> one returned, more available
    out = search_mod.search(conn, "q", mode="raw", k=1)
    page = out["meta"]["page"]
    assert page["returned"] == 1 and page["has_more"] is True
    assert page["next_cursor"] and page["offset"] == 0
    # second page: offset past the two hits -> nothing more
    out2 = search_mod.search(conn, "q", mode="raw", k=1, offset=1)
    assert out2["meta"]["page"]["has_more"] is False
    assert out2["meta"]["page"]["next_cursor"] is None


def test_cursor_roundtrip():
    for offset in (0, 5, 137):
        assert search_mod.decode_cursor(search_mod.encode_cursor(offset)) == offset
    with pytest.raises(ValueError):
        search_mod.decode_cursor("!!not-base64!!")


def test_agent_claims_reference_sources_by_handle():
    claims = [{
        "id": 7, "text": "Postgres handles joins well", "confidence": 0.8, "disputed": False,
        "evidence": [{"relation": "supports", "chunk_id": 42, "strength": 0.9, "document_url": "http://d"}],
    }]
    out = search_mod._agent_claims(claims)
    assert out[0]["id"] == "clm_7"
    edge = out[0]["evidence"][0]
    assert edge["source"] == "chk_42" and "document_url" not in edge
    assert edge["relation"] == "supports"


def test_full_mode_wires_synthesis(monkeypatch):
    monkeypatch.setattr(search_mod, "retrieve", lambda *a, **k: [FakeHit(1), FakeHit(2)])
    # stub every heavy stage so we test orchestration, not the components
    monkeypatch.setattr("app.expand.expand", lambda *a, **k: [])
    monkeypatch.setattr("app.rerank.rerank", lambda conn, q, hits, **k: hits)
    monkeypatch.setattr("app.evidence.claims.extract_claims", lambda *a, **k: [{"id": 7, "text": "c"}])
    monkeypatch.setattr("app.evidence.links.link_claim", lambda *a, **k: {})
    monkeypatch.setattr(
        "app.evidence.confidence.score_claim",
        lambda conn, cid: {"confidence": 0.8, "disputed": False},
    )
    monkeypatch.setattr("app.graph.query.graph_for_query", lambda conn, q, **k: {"nodes": [], "edges": []})
    monkeypatch.setattr(
        "app.synthesize.synthesize",
        lambda conn, q, claims, **k: {"answer": "cited answer [S1]", "sources": [{"index": 1}], "generator": "template"},
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE claim (id INTEGER PRIMARY KEY, text TEXT, confidence REAL, disputed INTEGER);"
        "CREATE TABLE evidence (id INTEGER PRIMARY KEY, claim_id INTEGER, chunk_id INTEGER, relation TEXT, strength REAL);"
        "CREATE TABLE chunk (id INTEGER PRIMARY KEY, document_id INTEGER);"
        "CREATE TABLE document (id INTEGER PRIMARY KEY, url TEXT);"
    )
    conn.execute("INSERT INTO claim VALUES (7, 'c', NULL, 0)")

    out = search_mod.search(conn, "q", mode="full", use_llm=False)
    assert out["answer"] == "cited answer [S1]"
    assert out["citations"] == [{"index": 1}]
    assert out["meta"]["generator"] == "template"
    assert out["claims"][0]["id"] == 7 and out["claims"][0]["confidence"] == 0.8
