"""Structured error envelope + taxonomy.

Every API error renders as one machine-parseable shape so an agent can branch on
``code`` and retry only when ``retryable`` is true:

    {"error": {"code": ..., "message": ..., "retryable": ..., "request_id": ...}}

Distinct codes separate the failure modes an agent handles differently — a
``timeout``/``upstream_error`` is worth retrying, an ``invalid_request`` or
``budget_exceeded`` is not.
"""

from __future__ import annotations

import uuid

CODES: dict[str, tuple[int, bool]] = {
    "invalid_request": (422, False),
    "not_found": (404, False),
    "unauthorized": (401, False),
    "rate_limited": (429, True),
    "budget_exceeded": (429, False),
    "timeout": (504, True),
    "upstream_error": (502, True),
    "internal": (500, True),
}


class ApiError(Exception):
    """Raise with a taxonomy ``code``; the handler renders the envelope."""

    def __init__(
        self, code: str, message: str, *, retryable: bool | None = None, status: int | None = None,
        retry_after: int | None = None,
    ):
        if code not in CODES:
            code = "internal"
        default_status, default_retryable = CODES[code]
        self.code = code
        self.message = message
        self.status = status or default_status
        self.retryable = default_retryable if retryable is None else retryable
        self.retry_after = retry_after
        super().__init__(message)


_STATUS_TO_CODE = {status: code for code, (status, _) in CODES.items()}


def code_for_status(status: int) -> str:
    return _STATUS_TO_CODE.get(status, "internal")


def new_request_id() -> str:
    return uuid.uuid4().hex


def envelope(code: str, message: str, retryable: bool, request_id: str) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "request_id": request_id,
        }
    }
