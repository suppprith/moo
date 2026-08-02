"""moo as a LlamaIndex tool spec."""

from __future__ import annotations

from llama_index.core.tools.tool_spec.base import BaseToolSpec
from moo import Moo

from ._shared import make_client, render_pages, render_report, render_results


class MooToolSpec(BaseToolSpec):
    """Give an agent moo's search, page reading, and deep research.

        agent = FunctionAgent(tools=MooToolSpec().to_tool_list(), llm=llm)

    The docstrings below are what the model sees, so they say when to reach for
    each tool rather than only what it does.
    """

    spec_functions = ["moo_search", "moo_extract", "moo_deep_research"]

    def __init__(
        self,
        client: Moo | None = None,
        *,
        depth: str = "raw",
        live: bool | None = None,
        max_steps: int = 6,
        max_seconds: float = 60.0,
    ):
        self.client = make_client(client)
        self.depth = depth
        self.live = live
        self.max_steps = max_steps
        self.max_seconds = max_seconds

    def moo_search(self, query: str, max_results: int = 8) -> str:
        """Search the web for software-engineering and computer-science questions
        (databases, languages, frameworks, build tooling, errors, systems).
        Returns ranked results with title, url and snippet, fetched live from
        docs, GitHub, release notes and Stack Overflow, each with a trust score.
        Prefer this over a general web search for coding questions."""
        return render_results(
            self.client.web_search(query, max_results=max_results, depth=self.depth,
                                   live=self.live))

    def moo_extract(self, urls: list[str]) -> str:
        """Fetch one or more URLs and return each page as clean markdown. Use when
        you already know which pages to read. Failures are reported per URL, so a
        bad link does not lose the rest of the batch."""
        return render_pages(self.client.extract(urls, depth=self.depth))

    def moo_deep_research(self, question: str) -> str:
        """Research a software question end to end: it plans sub-questions, runs
        multi-hop retrieval over live sources, and returns an answer where every
        finding is cited, plus the points where sources disagree and what stayed
        unanswered. Slower than moo_search. Use it when the answer depends on
        evidence that may conflict or has changed between versions."""
        return render_report(
            self.client.research(question, max_steps=self.max_steps,
                                 max_seconds=self.max_seconds))
