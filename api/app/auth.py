"""Optional API-key auth + per-key rate limiting & usage metering.

Off by default: with no keys configured, moo runs fully open — the local /
self-hosted mode. Two ways to switch it on:

- ``MOO_API_KEYS`` (comma-separated) for a fixed set, unchanged from before.
- Issued keys in the ``api_key`` table (``python -m app.keys create``), which a
  hosted instance needs: hashed at rest, revocable, with per-key limits, quotas
  and counters that survive a restart. Once an instance has issued a key it
  stays gated, so revoking the last one does not silently reopen it.

Either presents as ``Authorization: Bearer <key>`` or ``X-API-Key``.
``MOO_RATE_LIMIT_PER_MIN`` (default 60) is the fallback limit for keys that do
not set their own.

Metering counts requests per key; no query *content* is ever stored. Rate-limit
windows are per process, so several workers each get the configured limit.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from . import credits
from . import keys as key_store
from .errors import ApiError

_windows: dict[str, deque[float]] = defaultdict(deque)
_usage: dict[str, dict] = defaultdict(lambda: {"requests": 0})
_db_state: dict = {"checked_at": 0.0, "count": 0}
_DB_CACHE_SECONDS = 5.0


def _keys() -> set[str] | None:
    raw = os.environ.get("MOO_API_KEYS", "").strip()
    keys = {k.strip() for k in raw.split(",") if k.strip()}
    return keys or None


def _connect():
    from .db import get_connection

    return get_connection()


def _issued_keys() -> int:
    """How many issued keys exist, cached briefly so auth stays a memory hit on
    the hot path while a newly issued key still takes effect within seconds."""
    now = time.monotonic()
    if now - _db_state["checked_at"] >= _DB_CACHE_SECONDS:
        conn = _connect()
        try:
            _db_state["count"] = key_store.count(conn)
        finally:
            conn.close()
        _db_state["checked_at"] = now
    return _db_state["count"]


def enabled() -> bool:
    return _keys() is not None or _issued_keys() > 0


def active() -> bool:
    """Whether any check runs at all: keys are configured, or a demo limit is
    set and keyless callers have to be metered per visitor."""
    return enabled() or demo_limit() is not None


def _rate_limit() -> int:
    try:
        return int(os.environ.get("MOO_RATE_LIMIT_PER_MIN", "60"))
    except ValueError:
        return 60


def demo_limit() -> int | None:
    """Per-visitor limit for keyless calls, which is what makes a public
    playground safe to leave open. Unset (the default) means keyless requests
    are unlimited, which is right for a laptop and wrong for the internet."""
    raw = os.environ.get("MOO_DEMO_RATE_LIMIT_PER_MIN", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _client_ip(request) -> str:
    """The visitor, as far as we can tell behind a proxy. Only ever used as a
    rate-limit bucket, never stored."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def _extract_key(request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-api-key")


def presented_key(request) -> str | None:
    """The key on this request, however it was sent."""
    return _extract_key(request)


def _enforce_window(identity: str, limit: int) -> None:
    now = time.monotonic()
    window = _windows[identity]
    while window and now - window[0] >= 60:
        window.popleft()
    if len(window) >= limit:
        retry_after = max(1, int(60 - (now - window[0])))
        raise ApiError("rate_limited", f"rate limit {limit}/min exceeded", retry_after=retry_after)
    window.append(now)


def authenticate(request) -> dict:
    """Verify the request's key and enforce its rate limit, quota and credits.

    Returns ``{identity, key_id, row}``; ``identity`` is what metering is keyed
    on ("local" when auth is disabled) and ``key_id`` is set only for an issued
    key, so the caller can charge it. Raises ApiError (unauthorized /
    rate_limited / budget_exceeded) otherwise."""
    env_keys = _keys()
    key = _extract_key(request)

    if env_keys is None and _issued_keys() == 0:
        return _open_or_demo(request)

    if not key:
        demo = demo_limit()
        if demo is not None:
            return _open_or_demo(request)
        raise ApiError("unauthorized", "missing or invalid API key")

    if env_keys and key in env_keys:
        _enforce_window(key, _rate_limit())
        _usage[key]["requests"] += 1
        return {"identity": key, "key_id": None, "row": None}

    conn = _connect()
    try:
        row = key_store.lookup(conn, key)
        if row is None:
            raise ApiError("unauthorized", "missing or invalid API key")
        if row["quota"] is not None and row["requests"] >= row["quota"]:
            raise ApiError("budget_exceeded", f"key quota of {row['quota']} requests exhausted")
        row = credits.roll_period(conn, row)
        if credits.exhausted(row):
            raise ApiError(
                "budget_exceeded",
                f"monthly credits exhausted ({row['credits_included']} used); "
                f"they reset on {credits.period_end(row['period_start'])}",
            )
        identity = row["prefix"]
        _enforce_window(identity, row["rate_limit_per_min"] or _rate_limit())
        key_store.record_use(conn, row["id"])
    finally:
        conn.close()
    _usage[identity]["requests"] += 1
    return {"identity": identity, "key_id": row["id"], "row": row}


def _open_or_demo(request) -> dict:
    """A keyless call: free on a local instance, rate limited per visitor on a
    public playground."""
    demo = demo_limit()
    if demo is None:
        return {"identity": "local", "key_id": None, "row": None}
    identity = f"ip:{_client_ip(request)}"
    _enforce_window(identity, demo)
    _usage[identity]["requests"] += 1
    return {"identity": identity, "key_id": None, "row": None}


def check(request) -> str:
    """Authenticate and return just the metering identity."""
    return authenticate(request)["identity"]


def charge(key_id: int | None, endpoint: str, units: int) -> None:
    """Deduct a call's credits after it ran. No-op for env keys and open mode,
    which have no allowance to spend."""
    if key_id is None:
        return
    conn = _connect()
    try:
        credits.charge(conn, key_id, endpoint, units)
    finally:
        conn.close()


def usage() -> dict:
    """Masked per-key request counts for this process."""
    return {f"{k[:4]}…": v["requests"] for k, v in _usage.items()}


def issued_usage() -> list[dict]:
    """Persisted counters for issued keys. Never includes a key, only its prefix."""
    if _issued_keys() == 0:
        return []
    conn = _connect()
    try:
        return [
            {field: row[field] for field in
             ("prefix", "label", "requests", "quota", "rate_limit_per_min",
              "created_at", "last_used_at")}
            for row in key_store.list_keys(conn)
        ]
    finally:
        conn.close()


def reset() -> None:
    """Clear in-memory rate windows, usage, and the issued-key cache (tests)."""
    _windows.clear()
    _usage.clear()
    _db_state["checked_at"] = 0.0
    _db_state["count"] = 0
