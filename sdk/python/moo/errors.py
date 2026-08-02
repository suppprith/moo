"""Typed exceptions mapped from moo's structured error envelope.

Every API error arrives as ``{"error": {code, message, retryable, request_id}}``
with a stable ``code``. Each code gets its own exception class so callers branch
on the type; :attr:`MooError.retryable` says whether a retry can help, and
:attr:`MooError.request_id` is the id to quote when reporting a failure.
"""

from __future__ import annotations

from typing import Any


class MooError(Exception):
    """Base for every moo failure."""

    code = "internal"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
        request_id: str | None = None,
        status: int | None = None,
        retry_after: int | None = None,
        body: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code or type(self).code
        self.retryable = retryable
        self.request_id = request_id
        self.status = status
        self.retry_after = retry_after
        self.body = body or {}

    def __str__(self) -> str:
        parts = [f"[{self.code}] {self.message}"]
        if self.status is not None:
            parts.append(f"(HTTP {self.status})")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return " ".join(parts)


class InvalidRequestError(MooError):
    code = "invalid_request"


class NotFoundError(MooError):
    code = "not_found"


class AuthenticationError(MooError):
    code = "unauthorized"


class RateLimitError(MooError):
    code = "rate_limited"


class BudgetExceededError(MooError):
    code = "budget_exceeded"


class TimeoutError(MooError):  # noqa: A001
    code = "timeout"


class UpstreamError(MooError):
    code = "upstream_error"


class InternalError(MooError):
    code = "internal"


class ConnectionError(MooError):  # noqa: A001
    """The request never reached moo (DNS, refused connection, read timeout)."""

    code = "connection_error"

    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


_BY_CODE = {
    cls.code: cls
    for cls in (
        InvalidRequestError,
        NotFoundError,
        AuthenticationError,
        RateLimitError,
        BudgetExceededError,
        TimeoutError,
        UpstreamError,
        InternalError,
    )
}


def from_envelope(
    body: Any, *, status: int | None = None, retry_after: int | None = None
) -> MooError:
    """Build the right exception from a response body, tolerating non-envelope
    bodies (a proxy 502, an HTML error page) by falling back on the status."""
    error: dict[str, Any] = {}
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
    code = error.get("code") or _code_for_status(status)
    cls = _BY_CODE.get(code, MooError)
    message = error.get("message") or f"request failed with status {status}"
    retryable = error.get("retryable")
    if retryable is None:
        retryable = code in ("rate_limited", "timeout", "upstream_error", "internal")
    return cls(
        message,
        code=code,
        retryable=bool(retryable),
        request_id=error.get("request_id"),
        status=status,
        retry_after=retry_after,
        body=body if isinstance(body, dict) else {},
    )


def _code_for_status(status: int | None) -> str:
    return {
        401: "unauthorized",
        403: "unauthorized",
        404: "not_found",
        422: "invalid_request",
        429: "rate_limited",
        500: "internal",
        502: "upstream_error",
        504: "timeout",
    }.get(status or 0, "internal")
