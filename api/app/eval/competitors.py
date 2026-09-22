"""Competitor search-engine adapters for the eval flywheel.

Each adapter normalizes an external search API to one shape so the flywheel
can score every engine identically:

    {"results": [{"title", "url", "snippet"}], "answer": str | None}

Adapters are **key-gated and never raise**: no key -> the engine reports
unavailable and is skipped; an API failure returns ``None`` for that query
and is counted as an error. moo itself is always available (keyless,
store/cache; live when a provider is configured).

Both surfaces are covered: plain search (``exa_engine``, ``tavily_engine``) and
the deep/research modes (``exa_deep_engine``, ``tavily_research_engine``), so
moo's deep mode is compared against theirs rather than against basic search.

Keys (env): ``EXA_API_KEY``, ``TAVILY_API_KEY``.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("moo.eval.competitors")

TIMEOUT = 30.0
RESEARCH_TIMEOUT = 180.0


def moo_engine(conn, *, k: int = 8):
    """moo's own web-search surface — the same shape agents consume."""
    from ..websearch import web_search

    def run(query: str) -> dict | None:
        out = web_search(conn, query, k=k, depth="raw", use_llm=False)
        return {"results": out["results"], "answer": None}

    return run


def plain_search_engine(*, k: int = 8, provider=None):
    """The results a plain web-search tool hands an agent: the discovery
    provider's own titles and snippets, with nothing of moo's on top (no fetch,
    no ranking, no highlights). Coding agents' built-in search is a search
    engine's result list, so this is the baseline "does moo beat just
    searching" is measured against. Unavailable without a search provider."""
    if provider is None:
        from ..live.providers import resolve_provider

        provider = resolve_provider()
    if provider is None:
        return None

    def run(query: str) -> dict | None:
        cands = provider.discover(query, count=k)
        if not cands:
            return None
        return {"results": [{"title": c.title, "url": c.url, "snippet": c.snippet}
                            for c in cands], "answer": None}

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


def tavily_research_engine(*, model: str | None = None, client=None):
    """Tavily's research endpoint, the fair comparison for moo's deep mode.

    The endpoint and payload are overridable by env (``TAVILY_RESEARCH_URL``,
    ``TAVILY_RESEARCH_MODEL``) because this is written against their published
    shape without a key to verify it; parsing tolerates several field names so a
    small API difference degrades to a thinner score rather than an exception.
    """
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return None
    url = os.environ.get("TAVILY_RESEARCH_URL", "https://api.tavily.com/research")
    model = model or os.environ.get("TAVILY_RESEARCH_MODEL", "mini")
    http = client or httpx.Client(timeout=RESEARCH_TIMEOUT)

    def run(query: str) -> dict | None:
        try:
            resp = http.post(url, headers={"Authorization": f"Bearer {key}"},
                             json={"query": query, "model": model, "stream": False})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("tavily research failed: %s", exc)
            return None
        return {"results": _normalize_rows(data), "answer": _first_text(
            data, ("answer", "result", "report", "content", "output"))}

    return run


def exa_deep_engine(*, k: int = 8, client=None):
    """Exa's deep search mode. Same caveat as the Tavily research adapter: the
    request type is env-overridable (``EXA_SEARCH_URL``, ``EXA_DEEP_TYPE``)."""
    key = os.environ.get("EXA_API_KEY")
    if not key:
        return None
    url = os.environ.get("EXA_SEARCH_URL", "https://api.exa.ai/search")
    search_type = os.environ.get("EXA_DEEP_TYPE", "deep")
    http = client or httpx.Client(timeout=RESEARCH_TIMEOUT)

    def run(query: str) -> dict | None:
        try:
            resp = http.post(
                url,
                headers={"x-api-key": key},
                json={"query": query, "type": search_type, "numResults": k,
                      "contents": {"text": {"maxCharacters": 800}}},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("exa deep failed: %s", exc)
            return None
        return {"results": _normalize_rows(data),
                "answer": _first_text(data, ("answer", "summary", "report"))}

    return run


def _normalize_rows(data: dict) -> list[dict]:
    """Pull result rows out of whichever key an engine used for them."""
    rows = []
    for field in ("results", "sources", "citations", "documents"):
        value = data.get(field)
        if isinstance(value, list):
            rows = value
            break
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            "title": row.get("title") or row.get("name") or "",
            "url": row.get("url") or row.get("link") or "",
            "snippet": str(row.get("content") or row.get("text") or row.get("snippet") or "")[:800],
        })
    return out


def _first_text(data: dict, fields: tuple[str, ...]) -> str | None:
    for field in fields:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = _first_text(value, ("answer", "text", "content", "summary"))
            if nested:
                return nested
    return None


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
