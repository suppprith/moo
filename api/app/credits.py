"""Credits: what a call costs, what is left, and where it went.

Counting bare requests cannot price a deep-research run against a snippet
search, so calls are weighted. The prices below are anchored to what the
alternatives charge for the same work, and to what moo actually spends: a fast
search is one fetch-and-rank pass, a deep run is a whole agent research loop.

    fast search        1     one live retrieval pass, no LLM
    extract            1     per URL
    deep research     10     plus 5 per step beyond the default, capped at 25
    drill-down         0     following a handle you already paid for

That last line is deliberate. Charging for `fetch_source` after a search would
tax the thing that makes moo worth using, which is chasing a citation to the
sentence it came from.

The free tier is a rolling 30-day window of ``MOO_FREE_CREDITS`` (default 1000),
reset lazily on the first call after the window closes, so no scheduler is
needed. A key with no allowance (``credits_included IS NULL``) is unmetered,
which is what self-hosted keys are.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta

DEFAULT_FREE_CREDITS = 1000
PERIOD_DAYS = 30

SEARCH_COST = 1
EXTRACT_COST_PER_URL = 1
RESEARCH_BASE_COST = 10
RESEARCH_COST_PER_EXTRA_STEP = 5
RESEARCH_MAX_COST = 25
RESEARCH_INCLUDED_STEPS = 6

# Route prefix to base cost. Handlers that know better set request.state.credits.
COSTS: dict[str, int] = {
    "/search": SEARCH_COST,
    "/search/stream": SEARCH_COST,
    "/v1/web_search": SEARCH_COST,
    "/v1/extract": EXTRACT_COST_PER_URL,
    "/research": RESEARCH_BASE_COST,
    "/research/stream": RESEARCH_BASE_COST,
}

# Free to call: introspection, account management, and following a handle.
FREE_PREFIXES = ("/health", "/contract", "/v1/tools", "/usage", "/account", "/signup",
                 "/docs", "/redoc", "/openapi", "/source/", "/chunk/", "/claim/",
                 "/graph", "/research/")


def free_credits() -> int:
    try:
        return int(os.environ.get("MOO_FREE_CREDITS", DEFAULT_FREE_CREDITS))
    except ValueError:
        return DEFAULT_FREE_CREDITS


def cost_for_path(path: str, method: str = "GET") -> int:
    """The base cost of a route, before a handler refines it."""
    path = path.rstrip("/") or "/"
    if path in COSTS:
        return COSTS[path]
    if any(path.startswith(prefix) for prefix in FREE_PREFIXES):
        return 0
    return 0


def research_cost(max_steps: int | None) -> int:
    """A deep run is priced by the work it is allowed to do."""
    steps = RESEARCH_INCLUDED_STEPS if max_steps is None else max_steps
    extra = max(0, steps - RESEARCH_INCLUDED_STEPS)
    return min(RESEARCH_MAX_COST, RESEARCH_BASE_COST + extra * RESEARCH_COST_PER_EXTRA_STEP)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=UTC)
    except ValueError:
        return None


def period_end(period_start: str | None) -> str | None:
    start = _parse(period_start)
    if start is None:
        return None
    return (start + timedelta(days=PERIOD_DAYS)).isoformat(timespec="seconds")


def roll_period(conn: sqlite3.Connection, row: dict) -> dict:
    """Start a new window if the current one has closed. Lazy, so an unused key
    costs nothing to keep and there is no scheduler to run."""
    if row.get("credits_included") is None:
        return row
    start = _parse(row.get("period_start"))
    now = _now()
    if start is not None and now - start < timedelta(days=PERIOD_DAYS):
        return row
    conn.execute(
        "UPDATE api_key SET credits_used = 0, period_start = ? WHERE id = ?",
        (now.isoformat(timespec="seconds"), row["id"]),
    )
    conn.commit()
    return {**row, "credits_used": 0, "period_start": now.isoformat(timespec="seconds")}


def remaining(row: dict) -> int | None:
    """Credits left in this window, or None when the key is unmetered."""
    included = row.get("credits_included")
    if included is None:
        return None
    return max(0, included - (row.get("credits_used") or 0))


def exhausted(row: dict) -> bool:
    left = remaining(row)
    return left is not None and left <= 0


def charge(conn: sqlite3.Connection, key_id: int, endpoint: str, credits: int) -> None:
    """Record one call and deduct its credits. Endpoint is the route template,
    never a query."""
    day = _now().date().isoformat()
    conn.execute(
        "INSERT INTO api_key_usage (key_id, day, endpoint, requests, credits) "
        "VALUES (?, ?, ?, 1, ?) "
        "ON CONFLICT(key_id, day, endpoint) DO UPDATE SET "
        "requests = requests + 1, credits = credits + excluded.credits",
        (key_id, day, endpoint, credits),
    )
    if credits:
        conn.execute("UPDATE api_key SET credits_used = credits_used + ? WHERE id = ?",
                     (credits, key_id))
    conn.commit()


def usage_breakdown(conn: sqlite3.Connection, key_id: int, *, days: int = 30) -> dict:
    """Per-endpoint totals and a daily series for the dashboard."""
    since = (_now() - timedelta(days=days)).date().isoformat()
    rows = conn.execute(
        "SELECT day, endpoint, requests, credits FROM api_key_usage "
        "WHERE key_id = ? AND day >= ? ORDER BY day",
        (key_id, since),
    ).fetchall()
    by_endpoint: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    for row in rows:
        endpoint = by_endpoint.setdefault(row["endpoint"], {"requests": 0, "credits": 0})
        endpoint["requests"] += row["requests"]
        endpoint["credits"] += row["credits"]
        day = by_day.setdefault(row["day"], {"requests": 0, "credits": 0})
        day["requests"] += row["requests"]
        day["credits"] += row["credits"]
    return {
        "endpoints": [{"endpoint": name, **totals} for name, totals in
                      sorted(by_endpoint.items(), key=lambda kv: -kv[1]["credits"])],
        "daily": [{"day": day, **totals} for day, totals in sorted(by_day.items())],
    }


def account_view(conn: sqlite3.Connection, row: dict) -> dict:
    """Everything the dashboard shows for one key."""
    row = roll_period(conn, row)
    return {
        "key": {
            "prefix": row["prefix"],
            "label": row["label"],
            "created_at": row["created_at"],
            "last_used_at": row["last_used_at"],
        },
        "credits": {
            "included": row["credits_included"],
            "used": row["credits_used"],
            "remaining": remaining(row),
            "period_start": row["period_start"],
            "period_end": period_end(row["period_start"]),
            "unmetered": row["credits_included"] is None,
        },
        "requests": row["requests"],
        "usage": usage_breakdown(conn, row["id"]),
        "prices": {
            "search": SEARCH_COST,
            "extract_per_url": EXTRACT_COST_PER_URL,
            "research": f"{RESEARCH_BASE_COST}-{RESEARCH_MAX_COST}",
            "drill_down": 0,
        },
    }
