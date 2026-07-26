"""Adaptive query routing.

One fixed retrieval pipeline treats "PG::DeadlockDetected error" and "why do
databases use write-ahead logging" identically — but the first is won by exact
keyword match (BM25) and the second by semantic similarity. This router maps
query features onto per-index RRF weights and a fan-out decision:

- ``exact``    identifier / error-string queries -> keyword-weighted
- ``semantic`` conceptual natural-language queries -> vector-weighted
- ``balanced`` everything else -> the classic 1:1 hybrid

Paragraph-length queries (agents paste whole error contexts) additionally skip
query-expansion fan-out: variants of a paragraph add noise, not recall. The
decision is surfaced in ``meta.routing`` so ranking is explainable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_IDENTIFIER_RES = [
    re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b"),
    re.compile(r"\b[a-z]+[A-Z][A-Za-z]+\b"),
    re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z]+\b"),
    re.compile(r"\b\w+\.\w+\.\w+\b"),
    re.compile(r"::|->\w|\w\(\)"),
    re.compile(r"\b[A-Z][A-Z0-9]+_[A-Z0-9_]+\b"),
]
_ERROR_RE = re.compile(
    r"\b(?:error|exception|traceback|panic|fatal|segfault|stack\s*trace|errno|"
    r"E\d{3,5}|0x[0-9a-f]{4,})\b|exit\s+code\s+\d+",
    re.I,
)
_QUOTED_RE = re.compile(r"\"[^\"]{4,}\"|'[^']{4,}'")

SEMANTIC_MIN_WORDS = 10
PARAGRAPH_WORDS = 30

WEIGHTS = {
    "exact": (0.9, 1.6),
    "semantic": (1.3, 0.8),
    "balanced": (1.0, 1.0),
}


@dataclass
class Routing:
    strategy: str
    vector_weight: float
    keyword_weight: float
    fan_out: bool
    signals: list[str]


def _has_identifier(query: str) -> bool:
    return any(p.search(query) for p in _IDENTIFIER_RES)


def route(query: str, intent: str | None = None) -> Routing:
    """Pick the retrieval strategy for a query. ``intent`` (from understand())
    refines the call: troubleshooting leans exact, definition/why lean
    semantic."""
    signals: list[str] = []
    words = len(query.split())

    if _has_identifier(query):
        signals.append("identifier")
    if _ERROR_RE.search(query):
        signals.append("error")
    if _QUOTED_RE.search(query):
        signals.append("quoted")

    if signals:
        strategy = "exact"
    elif words >= SEMANTIC_MIN_WORDS or intent in ("definition", "why", "comparison"):
        strategy = "semantic"
        signals.append("conceptual")
    else:
        strategy = "balanced"

    if intent == "troubleshooting" and strategy != "exact":
        strategy = "balanced"
        signals.append("troubleshooting")

    vector_weight, keyword_weight = WEIGHTS[strategy]
    fan_out = words < PARAGRAPH_WORDS
    if not fan_out:
        signals.append("paragraph")
    return Routing(strategy, vector_weight, keyword_weight, fan_out, signals)
