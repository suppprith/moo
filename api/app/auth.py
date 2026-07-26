"""Optional API-key auth + per-key rate limiting & usage metering.

Off by default: with no keys configured, moo runs fully open — the local /
self-hosted mode. Set ``MOO_API_KEYS`` (comma-separated) to require a key on the
data endpoints; ``MOO_RATE_LIMIT_PER_MIN`` sets the per-key request limit
(default 60). A key is presented as ``Authorization: Bearer <key>`` or
``X-API-Key``.

Metering counts requests per key (for later inspection via ``/usage``); no query
*content* is ever stored.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from .errors import ApiError

_windows: dict[str, deque[float]] = defaultdict(deque)
_usage: dict[str, dict] = defaultdict(lambda: {"requests": 0})


def _keys() -> set[str] | None:
    raw = os.environ.get("MOO_API_KEYS", "").strip()
    keys = {k.strip() for k in raw.split(",") if k.strip()}
    return keys or None


def enabled() -> bool:
    return _keys() is not None


def _rate_limit() -> int:
    try:
        return int(os.environ.get("MOO_RATE_LIMIT_PER_MIN", "60"))
    except ValueError:
        return 60


def _extract_key(request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-api-key")


def check(request) -> str:
    """Verify the request's key and enforce the per-key rate limit. Returns the
    key ("local" when auth is disabled). Raises ApiError (unauthorized /
    rate_limited) otherwise."""
    keys = _keys()
    if keys is None:
        return "local"
    key = _extract_key(request)
    if not key or key not in keys:
        raise ApiError("unauthorized", "missing or invalid API key")

    limit = _rate_limit()
    now = time.monotonic()
    window = _windows[key]
    while window and now - window[0] >= 60:
        window.popleft()
    if len(window) >= limit:
        retry_after = max(1, int(60 - (now - window[0])))
        raise ApiError("rate_limited", f"rate limit {limit}/min exceeded", retry_after=retry_after)
    window.append(now)
    _usage[key]["requests"] += 1
    return key


def usage() -> dict:
    """Masked per-key request counts (keys are truncated)."""
    return {f"{k[:4]}…": v["requests"] for k, v in _usage.items()}


def reset() -> None:
    """Clear in-memory rate windows + usage (tests)."""
    _windows.clear()
    _usage.clear()
