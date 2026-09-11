"""Pluggable URL-discovery providers.

Given a query, a provider returns candidate URLs from an existing web index.
This is the honest shape of solo "live crawl": we don't index the web, we
discover through someone's index and add our own fetch/evidence layer on top.

Providers never raise — a discovery failure returns ``[]`` and the caller
falls back to whatever is already in the store.

Config (env, mirrors the MOO_LLM_* convention):

- ``MOO_SEARCH_PROVIDER``  explicit choice: ``searxng`` | ``brave`` | ``off``
- ``MOO_SEARXNG_URL``      base URL of a SearXNG instance (keyless, self-hosted)
- ``MOO_BRAVE_API_KEY``    Brave Search API key
  (``MOO_SEARCH_URL`` / ``MOO_SEARCH_API_KEY`` are accepted aliases)

With no explicit provider, whichever credential is present wins (key -> brave,
url -> searxng); with neither, ``resolve_provider()`` returns ``None`` and
live retrieval is simply unavailable (callers use the store).
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from ..env import load_dotenv

log = logging.getLogger("moo.live.providers")

DEFAULT_TIMEOUT = 10.0

_STATS = {"calls": 0, "errors": 0}


def get_stats() -> dict[str, int]:
    return dict(_STATS)


def reset_stats() -> None:
    _STATS["calls"] = 0
    _STATS["errors"] = 0


@dataclass
class Candidate:
    """One discovered URL, before the source policy has looked at it."""

    url: str
    title: str = ""
    snippet: str = ""
    rank: int = 0


class Provider(ABC):
    """A URL-discovery backend."""

    name: str = "provider"

    @abstractmethod
    def discover(self, query: str, *, count: int = 12) -> list[Candidate]:
        """Return up to ``count`` candidates. Must not raise."""


def _dedupe(cands: list[Candidate], count: int) -> list[Candidate]:
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in cands:
        u = c.url.strip()
        if not u.startswith(("http://", "https://")) or u in seen:
            continue
        seen.add(u)
        out.append(c)
        if len(out) >= count:
            break
    return out


class SearxngProvider(Provider):
    """Self-hosted SearXNG metasearch — the keyless path.

    Needs an instance with the JSON format enabled (``search.formats: [json]``
    in settings.yml); public instances usually block ``format=json``, so this
    is meant for a self-hosted one (single docker command, see README).
    """

    name = "searxng"

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT, client=None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def discover(self, query: str, *, count: int = 12) -> list[Candidate]:
        _STATS["calls"] += 1
        try:
            resp = self._client.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json", "safesearch": 0},
            )
            resp.raise_for_status()
            results = (resp.json() or {}).get("results", [])
        except Exception as exc:  # noqa: BLE001
            _STATS["errors"] += 1
            log.warning("searxng discovery failed: %s", exc)
            return []
        cands = [
            Candidate(
                url=r.get("url", ""),
                title=r.get("title", "") or "",
                snippet=r.get("content", "") or "",
                rank=i,
            )
            for i, r in enumerate(results)
        ]
        return _dedupe(cands, count)


class BraveProvider(Provider):
    """Brave Search API (bring-your-own-key; generous free tier)."""

    name = "brave"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, *, timeout: float = DEFAULT_TIMEOUT, client=None) -> None:
        self.api_key = api_key
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def discover(self, query: str, *, count: int = 12) -> list[Candidate]:
        _STATS["calls"] += 1
        try:
            resp = self._client.get(
                self.ENDPOINT,
                params={"q": query, "count": min(count, 20)},
                headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
            )
            resp.raise_for_status()
            results = ((resp.json() or {}).get("web") or {}).get("results", [])
        except Exception as exc:  # noqa: BLE001
            _STATS["errors"] += 1
            log.warning("brave discovery failed: %s", exc)
            return []
        cands = [
            Candidate(
                url=r.get("url", ""),
                title=r.get("title", "") or "",
                snippet=r.get("description", "") or "",
                rank=i,
            )
            for i, r in enumerate(results)
        ]
        return _dedupe(cands, count)


def resolve_provider() -> Provider | None:
    """Build the configured provider from env, or None when live search is off."""
    # Read api/.env here rather than trusting the entrypoint to: the eval CLIs
    # never call ensure_ready(), and a gate that silently runs store-only then
    # reports "no search provider" for a provider that is configured.
    load_dotenv()
    name = os.environ.get("MOO_SEARCH_PROVIDER", "").strip().lower()
    url = os.environ.get("MOO_SEARXNG_URL") or os.environ.get("MOO_SEARCH_URL")
    key = os.environ.get("MOO_BRAVE_API_KEY") or os.environ.get("MOO_SEARCH_API_KEY")

    if name in ("off", "none", "store"):
        return None
    if name == "searxng":
        return SearxngProvider(url) if url else None
    if name == "brave":
        return BraveProvider(key) if key else None
    if name:
        log.warning("unknown MOO_SEARCH_PROVIDER %r; live search disabled", name)
        return None
    if key:
        return BraveProvider(key)
    if url:
        return SearxngProvider(url)
    return None
