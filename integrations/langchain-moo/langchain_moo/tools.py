"""moo as LangChain tools.

Each tool returns ``(text, artifact)``: readable text for the model, and the raw
moo payload as the artifact, so a chain can use handles, trust scores, evidence
and citations without re-parsing prose.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.callbacks import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from moo import Moo
from pydantic import BaseModel, ConfigDict, Field

from ._shared import async_twin, make_client, render_report, render_results


class _MooTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: Moo = Field(default_factory=Moo)
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def __init__(self, client: Moo | None = None, **kwargs: Any):
        super().__init__(client=make_client(client), **kwargs)


class SearchInput(BaseModel):
    query: str = Field(description="the search query")
    max_results: int = Field(8, description="how many results to return (1-50)")


class MooSearchTool(_MooTool):
    """Search the live web, scoped to software and computer science."""

    name: str = "moo_search"
    description: str = (
        "Search the web for software-engineering and computer-science questions "
        "(databases, languages, frameworks, build tooling, errors, systems). Returns "
        "ranked results with title, url and snippet, fetched live from docs, GitHub, "
        "release notes and Stack Overflow, each with a trust score. Prefer this over a "
        "general web search for coding questions."
    )
    args_schema: type[BaseModel] = SearchInput
    depth: str = "raw"
    live: bool | None = None

    def _run(
        self, query: str, max_results: int = 8,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> tuple[str, dict[str, Any]]:
        payload = self.client.web_search(query, max_results=max_results, depth=self.depth,
                                         live=self.live)
        return render_results(payload), payload

    async def _arun(
        self, query: str, max_results: int = 8,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> tuple[str, dict[str, Any]]:
        client = async_twin(self.client)
        try:
            payload = await client.web_search(query, max_results=max_results, depth=self.depth,
                                              live=self.live)
        finally:
            await client.aclose()
        return render_results(payload), payload


class ExtractInput(BaseModel):
    urls: list[str] = Field(description="up to 10 page urls to read")


class MooExtractTool(_MooTool):
    """Read specific pages instead of searching for them."""

    name: str = "moo_extract"
    description: str = (
        "Fetch one or more URLs and return each page as clean markdown. Use when you "
        "already know which pages to read. Failures are reported per URL, so a bad link "
        "does not lose the rest of the batch."
    )
    args_schema: type[BaseModel] = ExtractInput
    depth: str = "raw"

    def _run(
        self, urls: list[str], run_manager: CallbackManagerForToolRun | None = None
    ) -> tuple[str, dict[str, Any]]:
        payload = self.client.extract(urls, depth=self.depth)
        return _render_pages(payload), payload

    async def _arun(
        self, urls: list[str], run_manager: AsyncCallbackManagerForToolRun | None = None
    ) -> tuple[str, dict[str, Any]]:
        client = async_twin(self.client)
        try:
            payload = await client.extract(urls, depth=self.depth)
        finally:
            await client.aclose()
        return _render_pages(payload), payload


class ResearchInput(BaseModel):
    question: str = Field(description="the question to research")


class MooDeepResearchTool(_MooTool):
    """Hand off a whole question and get a cited report back."""

    name: str = "moo_deep_research"
    description: str = (
        "Research a software question end to end: it plans sub-questions, runs multi-hop "
        "retrieval over live sources, and returns an answer where every finding is cited, "
        "plus the points where sources disagree and what stayed unanswered. Slower than "
        "moo_search. Use it when the answer depends on evidence that may conflict or has "
        "changed between versions."
    )
    args_schema: type[BaseModel] = ResearchInput
    max_steps: int = 6
    max_seconds: float = 60.0

    def _run(
        self, question: str, run_manager: CallbackManagerForToolRun | None = None
    ) -> tuple[str, dict[str, Any]]:
        report = self.client.research(question, max_steps=self.max_steps,
                                      max_seconds=self.max_seconds)
        return render_report(report), report

    async def _arun(
        self, question: str, run_manager: AsyncCallbackManagerForToolRun | None = None
    ) -> tuple[str, dict[str, Any]]:
        client = async_twin(self.client)
        try:
            report = await client.research(question, max_steps=self.max_steps,
                                           max_seconds=self.max_seconds)
        finally:
            await client.aclose()
        return render_report(report), report


def _render_pages(payload: dict[str, Any]) -> str:
    parts = []
    for page in payload.get("results", []):
        if page.get("error"):
            parts.append(f"# {page.get('url')}\nfailed: {page['error'].get('message')}")
            continue
        parts.append(f"# {page.get('title') or page.get('url')}\n{page.get('url')}\n\n"
                     f"{page.get('markdown', '')}")
    notice = payload.get("notice")
    if notice:
        parts.append(notice)
    return "\n\n".join(parts).strip() or "No pages were read."


def moo_toolkit(client: Moo | None = None, **kwargs: Any) -> list[BaseTool]:
    """Every moo tool, sharing one client. Bind these to a model as-is."""
    client = make_client(client)
    return [
        MooSearchTool(client, **kwargs),
        MooExtractTool(client),
        MooDeepResearchTool(client),
    ]
