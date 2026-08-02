import asyncio
import json

import httpx
import pytest

from moo import AsyncMoo, Moo


def stub(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def recorder(payload=None, status=200):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = request.url
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=payload if payload is not None else {"ok": True})

    return handler, seen


def test_defaults_to_local_server(monkeypatch):
    monkeypatch.delenv("MOO_BASE_URL", raising=False)
    monkeypatch.delenv("MOO_API_KEY", raising=False)
    client = Moo()
    assert client.config.base_url == "http://127.0.0.1:8000"
    assert client.config.api_key is None


def test_env_configuration(monkeypatch):
    monkeypatch.setenv("MOO_BASE_URL", "https://api.example.com/")
    monkeypatch.setenv("MOO_API_KEY", "sk-env")
    client = Moo()
    assert client.config.base_url == "https://api.example.com"
    assert client.config.api_key == "sk-env"


def test_api_key_sent_as_bearer():
    handler, seen = recorder()
    client = Moo("sk-test", base_url="https://moo.test", http_client=stub(handler))
    client.health()
    assert seen["headers"]["authorization"] == "Bearer sk-test"


def test_search_sends_only_set_params():
    handler, seen = recorder({"query": "wal", "mode": "raw", "sources": []})
    client = Moo(base_url="https://moo.test", http_client=stub(handler))
    client.search("wal mode", mode="claims", k=5, live=False)
    params = dict(seen["url"].params)
    assert params == {"q": "wal mode", "mode": "claims", "k": "5", "live": "false"}
    assert seen["url"].path == "/search"


def test_web_search_posts_body():
    handler, seen = recorder({"query": "x", "results": []})
    client = Moo(base_url="https://moo.test", http_client=stub(handler))
    client.web_search("redis eviction", max_results=3, depth="claims")
    assert seen["method"] == "POST"
    assert seen["url"].path == "/v1/web_search"
    assert seen["body"] == {"query": "redis eviction", "max_results": 3, "depth": "claims"}


def test_extract_and_research_bodies():
    handler, seen = recorder({"results": []})
    client = Moo(base_url="https://moo.test", http_client=stub(handler))
    client.extract(["https://a.dev", "https://b.dev"], depth="claims")
    assert seen["body"] == {"urls": ["https://a.dev", "https://b.dev"], "depth": "claims"}

    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    client.research("why", max_steps=3, output_schema=schema)
    assert seen["body"] == {"question": "why", "max_steps": 3, "output_schema": schema}


def test_handle_endpoints_build_paths():
    handler, seen = recorder({"id": "chk_7"})
    client = Moo(base_url="https://moo.test", http_client=stub(handler))
    client.chunk("chk_7")
    assert seen["url"].path == "/chunk/chk_7"
    client.source("doc_2")
    assert seen["url"].path == "/source/doc_2"
    client.claim("clm_9")
    assert seen["url"].path == "/claim/clm_9"
    client.get_research("abc123")
    assert seen["url"].path == "/research/abc123"


def test_context_manager_closes_owned_client():
    with Moo(base_url="https://moo.test") as client:
        assert client._owns_client
    assert client._client.is_closed


def test_injected_client_is_not_closed():
    handler, _ = recorder()
    injected = stub(handler)
    client = Moo(base_url="https://moo.test", http_client=injected)
    client.close()
    assert not injected.is_closed


@pytest.mark.parametrize("method", ["search", "web_search", "research", "extract"])
def test_async_client_mirrors_sync_surface(method):
    assert hasattr(AsyncMoo, method)
    assert hasattr(Moo, method)


def test_async_call_roundtrip():
    handler, seen = recorder({"query": "q", "results": []})
    async def go():
        async with AsyncMoo(
            "sk-a", base_url="https://moo.test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as client:
            return await client.web_search("vacuum full")

    out = asyncio.run(go())
    assert out["results"] == []
    assert seen["headers"]["authorization"] == "Bearer sk-a"
