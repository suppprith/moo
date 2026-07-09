"""Shared HTTP fetcher for all connectors (SUP-72).

Responsibilities:
- per-host rate limiting (min interval between requests to the same host)
- robots.txt respect (can be bypassed for documented APIs)
- retries with exponential backoff, honoring ``Retry-After`` on 429
- on-disk response cache with ETag / Last-Modified conditional requests, so
  re-running a crawl skips unchanged pages (304 -> served from cache)

Nothing here raises on a failed request: callers get a ``FetchResult`` with
``ok=False`` and the error is logged, so a single bad URL never crashes a run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("moo.ingest.fetcher")

API_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = API_DIR / ".cache" / "http"
USER_AGENT = "moo-search/0.1 (+https://github.com/suppprith/moo-search)"


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    headers: dict[str, str]        # keys normalized to lowercase
    from_cache: bool = False       # served from disk (304 or offline hit)
    not_modified: bool = False     # server said 304
    ok: bool = True
    error: str | None = None

    def __post_init__(self) -> None:
        # httpx lowercases keys when cast to dict, but hand-built dicts (cache
        # fallbacks, tests) may not — normalize so lookups are predictable.
        self.headers = {k.lower(): v for k, v in self.headers.items()}

    def header(self, name: str, default: str = "") -> str:
        """Case-insensitive header lookup."""
        return self.headers.get(name.lower(), default)

    @property
    def json(self) -> object:
        return json.loads(self.text) if self.text else None


class Fetcher:
    """A polite, caching HTTP client shared by connectors."""

    def __init__(
        self,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        *,
        min_interval: float = 1.0,
        max_retries: int = 3,
        timeout: float = 30.0,
        obey_robots: bool = True,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.obey_robots = obey_robots
        base_headers = {"User-Agent": USER_AGENT}
        if headers:
            base_headers.update(headers)
        self._client = httpx.Client(timeout=timeout, headers=base_headers, follow_redirects=True)
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # -- context manager -----------------------------------------------------
    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- caching -------------------------------------------------------------
    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> dict | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, url: str, status: int, headers: httpx.Headers, text: str) -> None:
        entry = {
            "url": url,
            "status": status,
            "fetched_at": time.time(),
            "etag": headers.get("ETag"),
            "last_modified": headers.get("Last-Modified"),
            "content_type": headers.get("Content-Type"),
            "text": text,
        }
        try:
            self._cache_path(url).write_text(
                json.dumps(entry, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:  # noqa: BLE001 - caching is best-effort
            log.warning("cache write failed for %s: %s", url, exc)

    # -- politeness ----------------------------------------------------------
    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def _allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            robots_url = f"{parts.scheme}://{host}/robots.txt"
            try:
                resp = self._client.get(robots_url)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None  # no robots -> allow
            except httpx.HTTPError as exc:
                log.debug("robots fetch failed for %s: %s", host, exc)
                rp = None
            self._robots[host] = rp
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    # -- fetch ---------------------------------------------------------------
    def get(self, url: str, *, obey_robots: bool | None = None) -> FetchResult:
        """GET a URL with caching + politeness. Never raises."""
        obey = self.obey_robots if obey_robots is None else obey_robots
        if obey and not self._allowed_by_robots(url):
            log.info("robots.txt disallows %s", url)
            return FetchResult(url, 0, "", {}, ok=False, error="disallowed by robots.txt")

        cached = self._read_cache(url)
        cond: dict[str, str] = {}
        if cached:
            if cached.get("etag"):
                cond["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                cond["If-Modified-Since"] = cached["last_modified"]

        host = urlsplit(url).netloc
        backoff = 1.0
        for attempt in range(1, self.max_retries + 1):
            self._throttle(host)
            try:
                resp = self._client.get(url, headers=cond)
            except httpx.HTTPError as exc:
                log.warning("fetch error %s (%d/%d): %s", url, attempt, self.max_retries, exc)
                if attempt == self.max_retries:
                    if cached:  # fall back to stale cache rather than nothing
                        return FetchResult(
                            url, cached["status"], cached["text"],
                            {"Content-Type": cached.get("content_type", "")},
                            from_cache=True, ok=True,
                        )
                    return FetchResult(url, 0, "", {}, ok=False, error=str(exc))
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 304 and cached:
                return FetchResult(
                    url, cached["status"], cached["text"],
                    {"Content-Type": cached.get("content_type", "")},
                    from_cache=True, not_modified=True, ok=True,
                )

            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if (retry_after or "").isdigit() else backoff
                log.warning("status %d for %s, backing off %.1fs", resp.status_code, url, delay)
                if attempt == self.max_retries:
                    return FetchResult(
                        url, resp.status_code, "", dict(resp.headers),
                        ok=False, error=f"HTTP {resp.status_code}",
                    )
                time.sleep(delay)
                backoff *= 2
                continue

            if resp.status_code >= 400:
                log.warning("status %d for %s (not retrying)", resp.status_code, url)
                return FetchResult(
                    url, resp.status_code, "", dict(resp.headers),
                    ok=False, error=f"HTTP {resp.status_code}",
                )

            self._write_cache(url, resp.status_code, resp.headers, resp.text)
            return FetchResult(url, resp.status_code, resp.text, dict(resp.headers))

        # unreachable
        return FetchResult(url, 0, "", {}, ok=False, error="exhausted retries")
