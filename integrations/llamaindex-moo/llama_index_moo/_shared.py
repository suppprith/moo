"""Client wiring and result rendering shared by the retriever, reader and tools."""

from __future__ import annotations

from typing import Any

from moo import Moo

MAX_SNIPPET = 500


def make_client(client: Moo | None = None, **kwargs: Any) -> Moo:
    """Use the caller's client, or build one from the usual env configuration."""
    return client or Moo(**kwargs)


def result_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields that make a moo result worth more than a link: the handle
    to drill into, how much the source is trusted, whether the page looked like
    it was trying to talk to the agent, and when it was fetched."""
    keys = ("id", "url", "title", "source_type", "trust_score", "relevance", "score",
            "fetched_at", "suspicious")
    return {key: row[key] for key in keys if row.get(key) is not None}


def render_results(payload: dict[str, Any]) -> str:
    """Render web-search rows as numbered markdown for a model to read."""
    rows = payload.get("results") or []
    if not rows:
        return "No results."
    lines = []
    for n, row in enumerate(rows, 1):
        header = f"[{n}] {row.get('title') or row.get('url')}"
        trust = row.get("trust_score")
        if trust is not None:
            header += f" (trust {trust:.2f})"
        if row.get("suspicious"):
            header += " (flagged: page contains prompt-injection patterns)"
        lines.append(header)
        lines.append(row.get("url", ""))
        if row.get("snippet"):
            lines.append(row["snippet"][:MAX_SNIPPET])
        lines.append("")
    if payload.get("notice"):
        lines.append(payload["notice"])
    return "\n".join(lines).strip()


def render_report(report: dict[str, Any]) -> str:
    """Render a research report the way an agent should read it: the answer, then
    what the sources disagree about, then what stayed unanswered."""
    lines = [report.get("executive_answer") or "No answer could be grounded in sources."]

    disputed = report.get("disputed_points") or []
    if disputed:
        lines.append("\nWhere sources disagree:")
        for point in disputed:
            supports = ", ".join(f"[S{n}]" for n in point.get("supports", []))
            contradicts = ", ".join(f"[S{n}]" for n in point.get("contradicts", []))
            lines.append(f"- {point.get('text')} (supported by {supports or 'none'}, "
                         f"contradicted by {contradicts or 'none'})")

    open_questions = report.get("open_questions") or []
    if open_questions:
        lines.append("\nStill open:")
        lines.extend(f"- {question}" for question in open_questions)

    sources = report.get("sources") or []
    if sources:
        lines.append("\nSources:")
        for source in sources:
            lines.append(f"[S{source.get('n')}] {source.get('title') or source.get('url')} "
                         f"- {source.get('url')}")

    if report.get("status") == "partial":
        lines.append("\nThe run hit its budget, so this report is partial.")
    return "\n".join(lines).strip()


def render_pages(payload: dict[str, Any]) -> str:
    parts = []
    for page in payload.get("results", []):
        if page.get("error"):
            parts.append(f"# {page.get('url')}\nfailed: {page['error'].get('message')}")
            continue
        parts.append(f"# {page.get('title') or page.get('url')}\n{page.get('url')}\n\n"
                     f"{page.get('markdown', '')}")
    if payload.get("notice"):
        parts.append(payload["notice"])
    return "\n\n".join(parts).strip() or "No pages were read."
