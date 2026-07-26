"""/v1/extract: URL(s) -> clean markdown + optional evidence.

Fetcher is stubbed (no network); document upsert, chunking, embedding, and
indexing are real — reuses the page fixtures from test_live_pipeline so the
extract surface and live search share one fetch->extract path.
"""

import pytest
from fastapi.testclient import TestClient

from app import extract as extract_mod
from app import mcp_server as m
from app.db import migrate
from app.extract import extract_urls
from app.index import vector
from app.main import app

from tests.test_live_pipeline import PAGES, PG_URL, SO_URL, FakeFetcher

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "extract.sqlite"
    migrate(db)
    c = vector.connect(db)
    yield c
    c.close()


def test_bad_depth_raises(conn):
    with pytest.raises(ValueError):
        extract_urls(conn, [PG_URL], depth="everything")


def test_empty_and_oversized_urls_raise(conn):
    with pytest.raises(ValueError):
        extract_urls(conn, [])
    with pytest.raises(ValueError):
        extract_urls(conn, [f"https://example.com/{i}" for i in range(11)])


def test_blocked_and_malformed_urls_fail_per_url(conn):
    fetcher = FakeFetcher(PAGES)
    out = extract_urls(
        conn,
        ["https://youtube.com/watch?v=x", "not-a-url", PG_URL],
        fetcher=fetcher,
    )
    blocked, malformed, good = out["results"]
    assert blocked["ok"] is False and blocked["error"]["code"] == "invalid_request"
    assert malformed["ok"] is False and malformed["error"]["code"] == "invalid_request"
    assert good["ok"] is True
    assert fetcher.requests == [PG_URL]


def test_extract_end_to_end(conn):
    out = extract_urls(conn, [PG_URL, SO_URL], fetcher=FakeFetcher(PAGES))
    assert [e["ok"] for e in out["results"]] == [True, True]
    pg = out["results"][0]
    assert "vacuum" in pg["markdown"].lower()
    assert pg["document"].startswith("doc_")
    assert pg["chunks"] and all(c.startswith("chk_") for c in pg["chunks"])
    assert pg["from_cache"] is False
    assert pg["metadata"]["tier"] == "docs" and pg["metadata"]["source_type"] == "docs"
    assert pg["metadata"]["trust_score"] is not None
    assert "untrusted" in out["notice"]
    assert out["cost"] == {**out["cost"], "requested": 2, "fetched": 2, "failed": 0}
    assert conn.execute("SELECT count(*) FROM document").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] > 0


def test_second_extract_is_cache_served(conn):
    fetcher = FakeFetcher(PAGES)
    extract_urls(conn, [PG_URL], fetcher=fetcher)
    n_requests = len(fetcher.requests)
    out = extract_urls(conn, [PG_URL], fetcher=fetcher)
    assert out["results"][0]["from_cache"] is True
    assert out["results"][0]["markdown"]
    assert len(fetcher.requests) == n_requests


def test_force_bypasses_cache(conn):
    fetcher = FakeFetcher(PAGES)
    extract_urls(conn, [PG_URL], fetcher=fetcher)
    out = extract_urls(conn, [PG_URL], fetcher=fetcher, force=True)
    assert out["results"][0]["from_cache"] is False
    assert len(fetcher.requests) == 2


def test_failed_fetch_is_per_url(conn):
    out = extract_urls(
        conn, ["https://docs.python.org/3/missing.html", SO_URL], fetcher=FakeFetcher(PAGES)
    )
    bad, good = out["results"]
    assert bad["ok"] is False and bad["error"]["code"] == "upstream_error"
    assert bad["error"]["retryable"] is True
    assert good["ok"] is True
    assert out["cost"]["failed"] == 1


def test_duplicate_url_flagged(conn):
    out = extract_urls(conn, [PG_URL, PG_URL], fetcher=FakeFetcher(PAGES))
    first, dup = out["results"]
    assert first["ok"] is True
    assert dup["ok"] is False and "duplicate" in dup["error"]["message"]


def test_depth_claims_attaches_evidence(conn):
    out = extract_urls(conn, [PG_URL], depth="claims", use_llm=False, fetcher=FakeFetcher(PAGES))
    entry = out["results"][0]
    assert entry["ok"] is True
    claims = entry["claims"]
    assert claims, "heuristic extraction should find claims in the vacuuming page"
    for c in claims:
        assert c["id"].startswith("clm_")
    assert conn.execute("SELECT count(*) FROM claim").fetchone()[0] >= len(claims)


def test_endpoint_validates_body():
    r = client.post("/v1/extract", json={"urls": []})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_endpoint_returns_batch(monkeypatch):
    monkeypatch.setattr(
        "app.main.extract_urls",
        lambda conn, urls, **kw: {"results": [{"url": u, "ok": True} for u in urls],
                                  "notice": "n", "cost": {}},
    )
    monkeypatch.setattr("app.main.get_connection_for_search", lambda: _DummyConn())
    r = client.post("/v1/extract", json={"urls": ["https://sqlite.org/wal.html"]})
    assert r.status_code == 200
    assert r.json()["results"][0]["ok"] is True


class _DummyConn:
    def close(self):
        pass


def test_v1_tools_lists_both_defs():
    r = client.get("/v1/tools")
    names = [t["function"]["name"] for t in r.json()["tools"]]
    assert names == ["web_search", "extract"]


def test_mcp_extract_clamps_markdown(monkeypatch):
    monkeypatch.setattr(m, "_search_conn", lambda: _DummyConn())
    monkeypatch.setattr(m.extract_mod, "extract_urls", lambda conn, urls, **kw: {
        "results": [{"url": urls[0], "ok": True, "markdown": "x" * 20000,
                     "document": "doc_1", "chunks": ["chk_1"]}],
        "notice": "n", "cost": {}})
    out = m.extract(["https://sqlite.org/wal.html"], max_tokens=300)
    entry = out["results"][0]
    assert len(entry["markdown"]) < 20000
    assert entry["markdown_truncated"]["original_chars"] == 20000
    assert "doc_1" in entry["markdown_truncated"]["note"]
