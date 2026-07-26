"""Drop-in web_search adapter."""

import sqlite3

import pytest

from app import websearch


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("CREATE TABLE chunk (id INTEGER PRIMARY KEY, text TEXT);")
    c.execute("INSERT INTO chunk VALUES (75, 'Postgres  handles\n complex joins  well in practice')")
    c.execute("INSERT INTO chunk VALUES (76, 'MySQL is faster for simple reads')")
    c.commit()
    return c


def _fake_full_response(**over):
    resp = {
        "query": "q", "mode": "raw", "intent": "comparison", "answer": None,
        "claims": [], "graph": {"nodes": [], "edges": []}, "citations": [],
        "sources": [
            {"chunk_id": 75, "document_url": "https://d/1", "url_anchor": "https://d/1#joins",
             "title": "Joins", "source_type": "docs", "trust_score": 0.9, "score": 0.5},
            {"chunk_id": 76, "document_url": "https://news.ycombinator.com/item?id=1", "url_anchor": None,
             "title": None, "source_type": "hn_story", "trust_score": 0.2, "score": 0.4},
        ],
        "meta": {},
    }
    resp.update(over)
    return resp


def test_web_search_shape_has_title_url_snippet(conn, monkeypatch):
    monkeypatch.setattr(websearch, "run_search", lambda *a, **k: _fake_full_response())
    out = websearch.web_search(conn, "postgres vs mysql", k=2)
    assert out["query"] == "postgres vs mysql"
    r0 = out["results"][0]
    assert r0["title"] == "Joins"
    assert r0["url"] == "https://d/1#joins"
    assert r0["snippet"] == "Postgres handles complex joins well in practice"
    assert r0["id"] == "chk_75" and r0["source_type"] == "docs"
    assert "evidence" not in out


def test_title_falls_back_to_host(conn, monkeypatch):
    monkeypatch.setattr(websearch, "run_search", lambda *a, **k: _fake_full_response())
    out = websearch.web_search(conn, "q", k=2)
    assert out["results"][1]["url"] == "https://news.ycombinator.com/item?id=1"
    assert out["results"][1]["title"] == "news.ycombinator.com"


def test_depth_claims_attaches_evidence(conn, monkeypatch):
    resp = _fake_full_response(
        answer="Postgres wins on joins [S1]",
        claims=[{"id": 7, "text": "PG handles joins well", "confidence": 0.8, "disputed": False,
                 "evidence": [{"relation": "supports", "chunk_id": 75, "strength": 0.9}]}],
        citations=[{"index": 1}],
    )
    monkeypatch.setattr(websearch, "run_search", lambda *a, **k: resp)
    out = websearch.web_search(conn, "q", k=2, depth="claims")
    assert out["evidence"]["answer"] == "Postgres wins on joins [S1]"
    assert out["evidence"]["claims"][0]["id"] == "clm_7"
    assert out["evidence"]["claims"][0]["evidence"][0]["source"] == "chk_75"


def test_bad_depth_rejected(conn, monkeypatch):
    monkeypatch.setattr(websearch, "run_search", lambda *a, **k: _fake_full_response())
    with pytest.raises(ValueError):
        websearch.web_search(conn, "q", depth="bogus")


def test_anthropic_shape():
    rows = [{"title": "T", "url": "u", "snippet": "s", "id": "chk_1"}]
    blocks = websearch.to_anthropic_results(rows)
    assert blocks == [{"type": "web_search_result", "title": "T", "url": "u", "snippet": "s"}]


def test_openai_tool_definition_shape():
    tool = websearch.OPENAI_TOOL
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "web_search"
    assert "query" in tool["function"]["parameters"]["properties"]
