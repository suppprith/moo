"""Structured error envelope + taxonomy (SUP-107).

Every API error renders as one machine-parseable shape so an agent can branch on
``code`` and retry only when ``retryable`` is true:

    {"error": {"code": ..., "message": ..., "retryable": ..., "request_id": ...}}

Distinct codes separate the failure modes an agent handles differently — a
``timeout``/``upstream_error`` is worth retrying, an ``invalid_request`` or
``budget_exceeded`` is not.
"""

from __future__ import annotations

import uuid

# code -> (http_status, retryable_default)
CODES: dict[str, tuple[int, bool]] = {
    "invalid_request": (422, False),   # bad params / handle / cursor
    "not_found": (404, False),         # unknown handle / row
    "unauthorized": (401, False),      # missing/invalid API key (SUP-108)
    "rate_limited": (429, True),       # per-key quota (SUP-108)
    "budget_exceeded": (429, False),   # research run hit its cost/step budget (SUP-111/114)
    "timeout": (504, True),            # stage exceeded its deadline
    "upstream_error": (502, True),     # LLM / external upstream failed
    "internal": (500, True),           # unexpected
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
        self.retry_after = retry_after  # seconds; sets the Retry-After header
        super().__init__(message)


# HTTP status -> code, for translating framework-raised HTTPExceptions.
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
