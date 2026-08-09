"""Synchronous moo client."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from . import _ops as ops
from ._ops import Op
from ._transport import (
    Config,
    backoff_delay,
    check_stream_event,
    decode,
    headers,
    iter_sse,
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


class Moo:
    """Client for a moo instance.

    ``Moo()`` with no arguments talks to a local server on
    ``http://127.0.0.1:8000``; set ``base_url`` (or ``MOO_BASE_URL``) for a
    hosted one and ``api_key`` (or ``MOO_API_KEY``) when it requires a key.
    Retryable failures (429 and 5xx) are retried up to ``max_retries`` times,
    honoring ``Retry-After``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: httpx.Client | None = None,
    ):
        self.config: Config = resolve_config(api_key, base_url, timeout, max_retries)
        self._client = http_client or httpx.Client(timeout=self.config.timeout)
        self._owns_client = http_client is None

    def __enter__(self) -> Moo:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _call(self, op: Op) -> Any:
        url = self.config.base_url + op.path
        attempt = 0
        while True:
            try:
                response = self._client.request(
                    op.method, url, params=op.params or None, json=op.json,
                    headers=headers(self.config),
                )
            except httpx.HTTPError as exc:
                if attempt >= self.config.max_retries:
                    raise MooConnectionError(f"could not reach moo at {url}: {exc}") from exc
                time.sleep(backoff_delay(attempt))
                attempt += 1
                continue
            if should_retry(response.status_code, attempt, self.config.max_retries):
                delay = backoff_delay(attempt, retry_after_seconds(
                    response.headers.get("retry-after")))
                time.sleep(delay)
                attempt += 1
                continue
            return decode(response.status_code, response.content, response.headers)

    def _stream(self, op: Op) -> Iterator[StreamEvent]:
        url = self.config.base_url + op.path
        try:
            with self._client.stream(
                op.method, url, params=op.params or None, json=op.json,
                headers=headers(self.config, stream=True),
            ) as response:
                if response.status_code >= 400:
                    decode(response.status_code, response.read(), response.headers)
                for event, data in iter_sse(response.iter_lines()):
                    check_stream_event(event, data)
                    yield event, data
        except httpx.HTTPError as exc:
            raise MooConnectionError(f"stream from {url} failed: {exc}") from exc

    def health(self) -> dict[str, Any]:
        """Liveness plus the contract version the server speaks."""
        return self._call(ops.health())

    def usage(self) -> dict[str, Any]:
        """Masked per-key request counts."""
        return self._call(ops.usage())

    def metrics(self) -> dict[str, Any]:
        """Per-route p50/p95 latency and status counts over a rolling window."""
        return self._call(ops.metrics())

    def account_usage(self) -> dict[str, Any]:
        """Credits and per-endpoint usage for this key: what is left, when the
        window resets, and where the credits went."""
        return self._call(ops.account_usage())

    def contract(self) -> dict[str, Any]:
        """Machine-readable descriptor: contract version, handle formats, error
        envelope, and the MCP tool schemas."""
        return self._call(ops.contract())

    def tools(self) -> dict[str, Any]:
        """OpenAI-style function-tool definitions to register with an agent."""
        return self._call(ops.tools())

    def search(
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
        """Ranked evidence for a query. ``mode="raw"`` (the server default) makes
        zero LLM calls, ``claims`` adds the evidence layer, ``full`` adds a cited
        answer. ``format="agent"`` returns the compact handle-addressed shape."""
        return self._call(ops.search(
            q, mode=mode, k=k, format=format, fields=fields, offset=offset,
            cursor=cursor, live=live, highlights=highlights))

    def search_stream(
        self,
        q: str,
        *,
        mode: str | None = None,
        k: int | None = None,
        fields: str | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream search as ``(event, data)`` pairs: ``progress``, then ``source``
        and ``claim`` rows, then a terminal ``done``. A mid-stream failure raises."""
        return self._stream(ops.search_stream(q, mode=mode, k=k, fields=fields))

    def web_search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        depth: str | None = None,
        shape: str | None = None,
        live: bool | None = None,
    ) -> WebSearchResponse:
        """Drop-in replacement for an agent's ``web_search`` tool: returns
        ``{title, url, snippet}`` rows fetched live, plus moo's extras.
        ``shape="anthropic"`` renders ``web_search_result`` blocks."""
        return self._call(ops.web_search(
            query, max_results=max_results, depth=depth, shape=shape, live=live))

    def extract(
        self, urls: list[str], *, depth: str | None = None, force: bool | None = None
    ) -> ExtractResponse:
        """Fetch URLs and return clean markdown with store handles.
        ``depth="claims"`` also runs the evidence layer over each page."""
        return self._call(ops.extract(urls, depth=depth, force=force))

    def research(
        self,
        question: str,
        *,
        k: int | None = None,
        max_steps: int | None = None,
        max_seconds: float | None = None,
        use_llm: bool | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> ResearchReport:
        """Full deep-research run (plan, multi-hop loop, cited report). Pass
        ``output_schema`` for a caller-shaped ``structured`` section with
        per-field claim grounding."""
        return self._call(ops.research(
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
    ) -> Iterator[StreamEvent]:
        """Stream a research run: ``plan``, ``run``, one ``progress`` per step,
        then ``report`` and ``done``."""
        return self._stream(ops.research_stream(
            question, k=k, max_steps=max_steps, max_seconds=max_seconds,
            use_llm=use_llm, output_schema=output_schema))

    def get_research(self, run_id: str) -> ResearchReport:
        """Re-fetch a run's status and report (polling, resume)."""
        return self._call(ops.get_research(run_id))

    def source(self, handle: str) -> dict[str, Any]:
        """The full document behind a source (``doc_`` handle or rowid)."""
        return self._call(ops.source(handle))

    def chunk(self, handle: str) -> dict[str, Any]:
        """One chunk with full text and its surrounding context (``chk_`` handle)."""
        return self._call(ops.chunk(handle))

    def claim(self, handle: str) -> dict[str, Any]:
        """A claim with confidence and its full evidence set (``clm_`` handle)."""
        return self._call(ops.claim(handle))

    def graph(self, q: str, *, depth: int | None = None, cap: int | None = None
              ) -> dict[str, Any]:
        """The entity subgraph for a query, with claims attached to nodes and edges."""
        return self._call(ops.graph(q, depth=depth, cap=cap))

    def expand_graph(self, node: str, *, limit: int | None = None) -> dict[str, Any]:
        """The next ring of edges around an entity (``ent_`` handle or name)."""
        return self._call(ops.expand_graph(node, limit=limit))
