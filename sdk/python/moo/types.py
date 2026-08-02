"""Response shapes as TypedDicts.

Responses are plain dicts at runtime, so these are documentation and editor
help, not validation: every payload is ``total=False`` because moo's field
selection (``fields=``), formats (``format=agent``) and depths return subsets of
the full contract. Unknown keys are always allowed, so a server that adds a
field never breaks a pinned SDK.
"""

from __future__ import annotations

from typing import Any, TypedDict

StreamEvent = tuple[str, Any]


class Evidence(TypedDict, total=False):
    relation: str
    chunk_id: int
    strength: float | None
    document_url: str | None


class Claim(TypedDict, total=False):
    id: int
    text: str
    confidence: float | None
    disputed: bool
    evidence: list[Evidence]
    valid: dict[str, Any]
    superseded_by: str | None


class Source(TypedDict, total=False):
    chunk_id: int
    id: str
    document_url: str
    title: str | None
    source_type: str
    trust_score: float | None
    heading: str | None
    url_anchor: str
    score: float
    fetched_at: str | None
    suspicious: bool
    highlights: list[str]
    relevance: float
    rank_signals: dict[str, float]


class SearchResponse(TypedDict, total=False):
    query: str
    mode: str
    intent: str
    answer: str | None
    claims: list[Claim]
    graph: dict[str, Any]
    sources: list[Source]
    citations: list[dict[str, Any]]
    meta: dict[str, Any]


class WebSearchResult(TypedDict, total=False):
    title: str
    url: str
    snippet: str | None
    id: str
    source_type: str
    trust_score: float | None
    score: float
    fetched_at: str | None
    highlights: list[str]
    relevance: float
    suspicious: bool


class WebSearchResponse(TypedDict, total=False):
    query: str
    results: list[WebSearchResult]
    notice: str
    live: dict[str, Any]
    evidence: dict[str, Any]


class ExtractedPage(TypedDict, total=False):
    url: str
    title: str | None
    markdown: str
    document: str
    chunks: list[str]
    error: dict[str, Any]


class ExtractResponse(TypedDict, total=False):
    results: list[ExtractedPage]
    cost: dict[str, Any]
    notice: str


class Finding(TypedDict, total=False):
    id: str
    text: str
    confidence: float | None
    citations: list[int]
    grounded: bool
    independent_support: int


class DisputedPoint(TypedDict, total=False):
    id: str
    text: str
    supports: list[int]
    contradicts: list[int]


class ResearchSource(TypedDict, total=False):
    n: int
    id: str
    url: str
    title: str | None
    trust_tier: str


class ResearchReport(TypedDict, total=False):
    question: str
    executive_answer: str
    findings: list[Finding]
    disputed_points: list[DisputedPoint]
    open_questions: list[str]
    sources: list[ResearchSource]
    groundedness: dict[str, Any]
    structured: dict[str, Any]
    run_id: str
    status: str
    steps: list[dict[str, Any]]
    coverage: list[dict[str, Any]]
    budget: dict[str, Any]
    cost: dict[str, Any]
    generator: str
