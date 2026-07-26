"""Competitor search-engine adapters for the eval flywheel.

Each adapter normalizes an external search API to one shape so the flywheel
can score every engine identically:

    {"results": [{"title", "url", "snippet"}], "answer": str | None}

Adapters are **key-gated and never raise**: no key -> the engine reports
unavailable and is skipped; an API failure returns ``None`` for that query
and is counted as an error. moo itself is always available (keyless,
store/cache; live when a provider is configured).

Keys (env): ``EXA_API_KEY``, ``TAVILY_API_KEY``.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("moo.eval.competitors")

TIMEOUT = 30.0


def moo_engine(conn, *, k: int = 8):
    """moo's own web-search surface — the same shape agents consume."""
    from ..websearch import web_search

    def run(query: str) -> dict | None:
        out = web_search(conn, query, k=k, depth="raw", use_llm=False)
        return {"results": out["results"], "answer": None}

    return run


def exa_engine(*, k: int = 8, client=None):
    key = os.environ.get("EXA_API_KEY")
    if not key:
        return None
    http = client or httpx.Client(timeout=TIMEOUT)

    def run(query: str) -> dict | None:
        try:
            resp = http.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": key},
                json={"query": query, "numResults": k, "contents": {"text": {"maxCharacters": 500}}},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("exa failed: %s", exc)
            return None
        return {
            "results": [
                {"title": r.get("title") or "", "url": r.get("url") or "",
                 "snippet": (r.get("text") or "")[:500]}
                for r in data.get("results", [])
            ],
            "answer": None,
        }

    return run


def tavily_engine(*, k: int = 8, client=None):
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return None
    http = client or httpx.Client(timeout=TIMEOUT)

    def run(query: str) -> dict | None:
        try:
            resp = http.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {key}"},
                json={"query": query, "max_results": k, "include_answer": True},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("tavily failed: %s", exc)
            return None
        return {
            "results": [
                {"title": r.get("title") or "", "url": r.get("url") or "",
                 "snippet": (r.get("content") or "")[:500]}
                for r in data.get("results", [])
            ],
            "answer": data.get("answer"),
        }

    return run


def engines(conn, *, k: int = 8) -> dict:
    """All engines runnable right now: moo always; competitors when keyed."""
    out = {"moo": moo_engine(conn, k=k)}
    exa = exa_engine(k=k)
    if exa:
        out["exa"] = exa
    tavily = tavily_engine(k=k)
    if tavily:
        out["tavily"] = tavily
    return out
