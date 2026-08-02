"""Asynchronous moo client: same surface as :class:`moo.Moo`, awaited."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from . import _ops as ops
from ._ops import Op
from ._transport import (
    Config,
    SSEDecoder,
    backoff_delay,
    check_stream_event,
    decode,
    headers,
    resolve_config,
    retry_after_seconds,
    should_retry,
)
from .errors import ConnectionError as MooConnectionError
from .types import (
    ExtractResponse,
    ResearchReport,
    SearchResponse,
    StreamEvent,
    WebSearchResponse,
)


class AsyncMoo:
    """Async client for a moo instance. See :class:`moo.Moo` for configuration."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.config: Config = resolve_config(api_key, base_url, timeout, max_retries)
        self._client = http_client or httpx.AsyncClient(timeout=self.config.timeout)
        self._owns_client = http_client is None

    async def __aenter__(self) -> AsyncMoo:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _call(self, op: Op) -> Any:
        url = self.config.base_url + op.path
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    op.method, url, params=op.params or None, json=op.json,
                    headers=headers(self.config),
                )
            except httpx.HTTPError as exc:
                if attempt >= self.config.max_retries:
                    raise MooConnectionError(f"could not reach moo at {url}: {exc}") from exc
                await asyncio.sleep(backoff_delay(attempt))
                attempt += 1
                continue
            if should_retry(response.status_code, attempt, self.config.max_retries):
                delay = backoff_delay(attempt, retry_after_seconds(
                    response.headers.get("retry-after")))
                await asyncio.sleep(delay)
                attempt += 1
                continue
            return decode(response.status_code, response.content, response.headers)

    async def _stream(self, op: Op) -> AsyncIterator[StreamEvent]:
        url = self.config.base_url + op.path
        decoder = SSEDecoder()
        try:
            async with self._client.stream(
                op.method, url, params=op.params or None, json=op.json,
                headers=headers(self.config, stream=True),
            ) as response:
                if response.status_code >= 400:
                    decode(response.status_code, await response.aread(), response.headers)
                async for line in response.aiter_lines():
                    frame = decoder.feed(line)
                    if frame is None:
                        continue
                    check_stream_event(*frame)
                    yield frame
                trailing = decoder.flush()
                if trailing is not None:
                    check_stream_event(*trailing)
                    yield trailing
        except httpx.HTTPError as exc:
            raise MooConnectionError(f"stream from {url} failed: {exc}") from exc

    async def health(self) -> dict[str, Any]:
        """Liveness plus the contract version the server speaks."""
        return await self._call(ops.health())

    async def usage(self) -> dict[str, Any]:
        """Masked per-key request counts."""
        return await self._call(ops.usage())

    async def contract(self) -> dict[str, Any]:
        """Machine-readable descriptor: contract version, handle formats, error
        envelope, and the MCP tool schemas."""
        return await self._call(ops.contract())

    async def tools(self) -> dict[str, Any]:
        """OpenAI-style function-tool definitions to register with an agent."""
        return await self._call(ops.tools())

    async def search(
        self,
        q: str,
        *,
        mode: str | None = None,
        k: int | None = None,
        format: str | None = None,
        fields: str | None = None,
        offset: int | None = None,
        cursor: str | None = None,
        live: bool | None = None,
        highlights: bool | None = None,
    ) -> SearchResponse:
        """Ranked evidence for a query. ``mode="raw"`` makes zero LLM calls,
        ``claims`` adds the evidence layer, ``full`` adds a cited answer."""
        return await self._call(ops.search(
            q, mode=mode, k=k, format=format, fields=fields, offset=offset,
            cursor=cursor, live=live, highlights=highlights))

    def search_stream(
        self,
        q: str,
        *,
        mode: str | None = None,
        k: int | None = None,
        fields: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream search as ``(event, data)`` pairs: ``progress``, then ``source``
        and ``claim`` rows, then a terminal ``done``."""
        return self._stream(ops.search_stream(q, mode=mode, k=k, fields=fields))

    async def web_search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        depth: str | None = None,
        shape: str | None = None,
        live: bool | None = None,
    ) -> WebSearchResponse:
        """Drop-in replacement for an agent's ``web_search`` tool."""
        return await self._call(ops.web_search(
            query, max_results=max_results, depth=depth, shape=shape, live=live))

    async def extract(
        self, urls: list[str], *, depth: str | None = None, force: bool | None = None
    ) -> ExtractResponse:
        """Fetch URLs and return clean markdown with store handles."""
        return await self._call(ops.extract(urls, depth=depth, force=force))

    async def research(
        self,
        question: str,
        *,
        k: int | None = None,
        max_steps: int | None = None,
        max_seconds: float | None = None,
        use_llm: bool | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> ResearchReport:
        """Full deep-research run (plan, multi-hop loop, cited report)."""
        return await self._call(ops.research(
            question, k=k, max_steps=max_steps, max_seconds=max_seconds,
            use_llm=use_llm, output_schema=output_schema))

    def research_stream(
        self,
        question: str,
        *,
        k: int | None = None,
        max_steps: int | None = None,
        max_seconds: float | None = None,
        use_llm: bool | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a research run: ``plan``, ``run``, one ``progress`` per step,
        then ``report`` and ``done``."""
        return self._stream(ops.research_stream(
            question, k=k, max_steps=max_steps, max_seconds=max_seconds,
            use_llm=use_llm, output_schema=output_schema))

    async def get_research(self, run_id: str) -> ResearchReport:
        """Re-fetch a run's status and report (polling, resume)."""
        return await self._call(ops.get_research(run_id))

    async def source(self, handle: str) -> dict[str, Any]:
        """The full document behind a source (``doc_`` handle or rowid)."""
        return await self._call(ops.source(handle))

    async def chunk(self, handle: str) -> dict[str, Any]:
        """One chunk with full text and its surrounding context (``chk_`` handle)."""
        return await self._call(ops.chunk(handle))

    async def claim(self, handle: str) -> dict[str, Any]:
        """A claim with confidence and its full evidence set (``clm_`` handle)."""
        return await self._call(ops.claim(handle))

    async def graph(self, q: str, *, depth: int | None = None, cap: int | None = None
                    ) -> dict[str, Any]:
        """The entity subgraph for a query, with claims attached to nodes and edges."""
        return await self._call(ops.graph(q, depth=depth, cap=cap))

    async def expand_graph(self, node: str, *, limit: int | None = None) -> dict[str, Any]:
        """The next ring of edges around an entity (``ent_`` handle or name)."""
        return await self._call(ops.expand_graph(node, limit=limit))
