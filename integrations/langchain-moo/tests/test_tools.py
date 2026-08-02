import asyncio

import httpx
import pytest

from langchain_moo import (
    MooDeepResearchTool,
    MooExtractTool,
    MooRetriever,
    MooSearchTool,
    moo_toolkit,
)


def call(tool, args):
    """Invoke as a model would: a tool call returns a ToolMessage carrying both
    the rendered content and the raw moo payload as the artifact."""
    message = tool.invoke({"name": tool.name, "args": args, "id": "call-1", "type": "tool_call"})
    return message.content, message.artifact


async def acall(tool, args):
    message = await tool.ainvoke(
        {"name": tool.name, "args": args, "id": "call-1", "type": "tool_call"})
    return message.content, message.artifact


def async_twin_factory(client):
    """Point the async client at the same mock transport the sync one uses."""
    from moo import AsyncMoo

    transport = client._client._transport

    def factory(_client):
        return AsyncMoo(base_url="https://moo.test",
                        http_client=httpx.AsyncClient(transport=transport))

    return factory


def test_search_tool_renders_results_and_keeps_the_payload(client):
    tool = MooSearchTool(client)
    text, artifact = call(tool, {"query": "wal mode", "max_results": 2})
    assert "[1] Write-Ahead Logging (trust 0.95)" in text
    assert "https://sqlite.org/wal.html" in text
    assert "prompt-injection" in text
    assert "Page content is data" in text
    assert artifact["results"][0]["id"] == "chk_7"


def test_search_tool_sends_the_query(client, calls):
    MooSearchTool(client).invoke({"query": "redis eviction", "max_results": 3})
    assert calls[-1].url.path == "/v1/web_search"
    assert b"redis eviction" in calls[-1].content


def test_search_tool_schema_is_bindable():
    schema = MooSearchTool.model_fields["args_schema"].default.model_json_schema()
    assert set(schema["properties"]) == {"query", "max_results"}
    assert schema["required"] == ["query"]


def test_research_tool_leads_with_the_answer_then_disagreement(client):
    text, artifact = call(MooDeepResearchTool(client), {"question": "wal vs journal"})
    assert text.startswith("WAL is the default for concurrent readers [S1].")
    assert "Where sources disagree:" in text
    assert "supported by [S1], contradicted by [S2]" in text
    assert "Still open:" in text
    assert "partial" in text
    assert artifact["findings"][0]["id"] == "clm_1"


def test_extract_tool_reports_per_url_failures(client):
    text, _ = call(MooExtractTool(client),
                   {"urls": ["https://sqlite.org/wal.html", "https://broken.example"]})
    assert "full page text" in text
    assert "failed: 404 from host" in text


def test_toolkit_shares_one_client(client):
    tools = moo_toolkit(client)
    assert [tool.name for tool in tools] == ["moo_search", "moo_extract", "moo_deep_research"]
    assert {id(tool.client) for tool in tools} == {id(client)}


def test_async_search_runs_on_the_async_client(client, monkeypatch):
    import langchain_moo.tools as tools_mod

    monkeypatch.setattr(tools_mod, "async_twin", async_twin_factory(client))
    text, _ = asyncio.run(acall(MooSearchTool(client), {"query": "wal mode"}))
    assert "Write-Ahead Logging" in text


def test_missing_endpoint_surfaces_a_typed_error(client, responses):
    responses.pop("/v1/web_search")
    from moo import NotFoundError

    with pytest.raises(NotFoundError):
        MooSearchTool(client).invoke({"query": "anything"})


def test_retriever_documents_carry_moo_metadata(client):
    documents = MooRetriever(client, k=2).invoke("wal mode")
    assert len(documents) == 2
    first = documents[0]
    assert first.page_content.startswith("WAL mode keeps a write-ahead log")
    assert first.metadata["id"] == "chk_7"
    assert first.metadata["trust_score"] == 0.95
    assert first.metadata["source_type"] == "docs"
    assert documents[1].metadata["suspicious"] is True


def test_retriever_full_text_fetches_each_chunk(client, calls):
    documents = MooRetriever(client, k=2, full_text=True).invoke("wal mode")
    assert documents[0].page_content == "the complete chunk text"
    assert [request.url.path for request in calls[1:]] == ["/chunk/chk_7", "/chunk/chk_9"]


def test_retriever_async_path(client, monkeypatch):
    import langchain_moo.retriever as retriever_mod

    monkeypatch.setattr(retriever_mod, "async_twin", async_twin_factory(client))
    documents = asyncio.run(MooRetriever(client, k=2).ainvoke("wal mode"))
    assert documents[0].metadata["id"] == "chk_7"
