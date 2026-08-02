"""Configuration, retry policy, and response decoding shared by both clients.

Kept transport-agnostic so the sync and async clients differ only in how they
await I/O: the same config resolution, the same retry decisions, the same error
mapping, the same SSE framing.
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from .errors import MooError, from_envelope

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
MAX_BACKOFF = 20.0
USER_AGENT = "moo-python/0.1.0"


@dataclass
class Config:
    base_url: str
    api_key: str | None
    timeout: float
    max_retries: int


def resolve_config(
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> Config:
    """Explicit argument wins, then environment (``MOO_API_KEY``,
    ``MOO_BASE_URL``), then the local-server default. A self-hosted moo needs no
    key at all, so a missing key is not an error."""
    url = base_url or os.environ.get("MOO_BASE_URL") or DEFAULT_BASE_URL
    return Config(
        base_url=url.rstrip("/"),
        api_key=api_key or os.environ.get("MOO_API_KEY") or None,
        timeout=DEFAULT_TIMEOUT if timeout is None else timeout,
        max_retries=DEFAULT_MAX_RETRIES if max_retries is None else max(0, max_retries),
    )


def headers(cfg: Config, *, stream: bool = False) -> dict[str, str]:
    accept = "text/event-stream" if stream else "application/json"
    out = {"User-Agent": USER_AGENT, "Accept": accept}
    if cfg.api_key:
        out["Authorization"] = f"Bearer {cfg.api_key}"
    return out


def clean_params(params: dict[str, Any]) -> dict[str, Any]:
    """Drop unset options so the server's own defaults apply, and send booleans
    the way FastAPI parses them."""
    out = {}
    for key, value in params.items():
        if value is None:
            continue
        out[key] = "true" if value is True else "false" if value is False else value
    return out


def retry_after_seconds(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return None


def should_retry(status: int, attempt: int, max_retries: int) -> bool:
    return attempt < max_retries and status in RETRY_STATUSES


def backoff_delay(attempt: int, retry_after: int | None = None) -> float:
    """Honor ``Retry-After`` when the server sent one, otherwise exponential
    backoff with jitter so retrying clients do not resynchronize."""
    if retry_after is not None:
        return min(float(retry_after), MAX_BACKOFF)
    return min(MAX_BACKOFF, (2**attempt) * 0.5 * (1 + random.random()))


def decode(status: int, body: bytes, response_headers: Any) -> Any:
    """Return the parsed JSON body, or raise the mapped MooError."""
    try:
        payload = json.loads(body) if body else None
    except ValueError:
        payload = None
    if status >= 400:
        raise from_envelope(
            payload,
            status=status,
            retry_after=retry_after_seconds(_header(response_headers, "retry-after")),
        )
    if payload is None:
        raise MooError(f"empty response body (HTTP {status})", status=status, retryable=True)
    return payload


def _header(response_headers: Any, name: str) -> str | None:
    try:
        return response_headers.get(name)
    except AttributeError:
        return None


class SSEDecoder:
    """Line-at-a-time SSE parser, so sync and async streams share one framing.

    A frame ends at a blank line; ``data:`` lines accumulate. Frames whose data
    is not JSON are surfaced as raw strings rather than dropped.
    """

    def __init__(self) -> None:
        self._event = "message"
        self._data: list = []

    def feed(self, line: str) -> tuple[str, Any] | None:
        line = line.rstrip("\r")
        if not line:
            return self.flush()
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
        return None

    def flush(self) -> tuple[str, Any] | None:
        if not self._data:
            self._event = "message"
            return None
        frame = (self._event, _payload("\n".join(self._data)))
        self._event, self._data = "message", []
        return frame


def iter_sse(lines: Iterable[str]) -> Iterator[tuple[str, Any]]:
    """Turn raw SSE lines into ``(event, data)`` pairs."""
    decoder = SSEDecoder()
    for line in lines:
        frame = decoder.feed(line)
        if frame is not None:
            yield frame
    trailing = decoder.flush()
    if trailing is not None:
        yield trailing


def _payload(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def check_stream_event(event: str, data: Any) -> None:
    """A stream commits HTTP 200 before it can fail, so failures arrive in-band
    as an ``error`` event carrying the usual envelope."""
    if event == "error":
        raise from_envelope(data, status=None)
