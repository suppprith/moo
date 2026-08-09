"""Per-endpoint latency and status counters.

An agent developer deciding whether to route production traffic here wants two
numbers before anything else: how fast it answers and how often it fails. This
keeps both in memory, per route, over a rolling window, and serves them at
`/metrics` so a status page or a monitor has something to read.

What it stores is deliberately thin: a route template, a status class, and a
duration. No query, no key, no client. Counters are per process, so several
workers each report their own share.
"""

from __future__ import annotations

import threading
import time
from collections import deque

WINDOW = 500
ERROR_WINDOW_SECONDS = 300.0

_lock = threading.Lock()
_samples: dict[str, deque[float]] = {}
_counts: dict[str, dict[str, int]] = {}
_recent_5xx: deque[float] = deque()
_started = time.time()


def _key(method: str, route: str) -> str:
    return f"{method} {route}"


def _class_of(status_code: int) -> str:
    if status_code >= 500:
        return "5xx"
    if status_code >= 400:
        return "4xx"
    return "2xx"


def record(method: str, route: str, status_code: int, elapsed_ms: float) -> None:
    key = _key(method, route)
    now = time.time()
    with _lock:
        samples = _samples.setdefault(key, deque(maxlen=WINDOW))
        samples.append(elapsed_ms)
        counts = _counts.setdefault(key, {"requests": 0, "2xx": 0, "4xx": 0, "5xx": 0})
        counts["requests"] += 1
        counts[_class_of(status_code)] += 1
        if status_code >= 500:
            _recent_5xx.append(now)
        while _recent_5xx and now - _recent_5xx[0] > ERROR_WINDOW_SECONDS:
            _recent_5xx.popleft()


def percentile(values, p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
    return round(ordered[idx], 1)


def errors_5xx_recent() -> int:
    """5xx responses inside the alert window, which is the one number an alert
    rule needs to fire on."""
    now = time.time()
    with _lock:
        while _recent_5xx and now - _recent_5xx[0] > ERROR_WINDOW_SECONDS:
            _recent_5xx.popleft()
        return len(_recent_5xx)


def snapshot() -> dict:
    with _lock:
        routes = {}
        totals = {"requests": 0, "2xx": 0, "4xx": 0, "5xx": 0}
        for key, samples in _samples.items():
            counts = _counts.get(key, {})
            routes[key] = {
                "requests": counts.get("requests", 0),
                "p50_ms": percentile(samples, 50),
                "p95_ms": percentile(samples, 95),
                "max_ms": round(max(samples), 1) if samples else 0.0,
                "2xx": counts.get("2xx", 0),
                "4xx": counts.get("4xx", 0),
                "5xx": counts.get("5xx", 0),
            }
            for field in totals:
                totals[field] += counts.get(field, 0)
    return {
        "uptime_seconds": round(time.time() - _started, 1),
        "window": WINDOW,
        "totals": totals,
        "errors_5xx_recent": errors_5xx_recent(),
        "error_window_seconds": ERROR_WINDOW_SECONDS,
        "routes": dict(sorted(routes.items())),
    }


def uptime_seconds() -> float:
    return round(time.time() - _started, 1)


def reset() -> None:
    with _lock:
        _samples.clear()
        _counts.clear()
        _recent_5xx.clear()
