"""Structured error envelope + request ids (SUP-107)."""

from fastapi.testclient import TestClient

from app import errors
from app.main import app

client = TestClient(app)


# ---- taxonomy unit tests ----------------------------------------------------

def test_apierror_defaults_from_code():
    e = errors.ApiError("timeout", "took too long")
    assert e.status == 504 and e.retryable is True
    e2 = errors.ApiError("budget_exceeded", "out of budget")
    assert e2.status == 429 and e2.retryable is False


def test_apierror_unknown_code_falls_back_to_internal():
    e = errors.ApiError("nonsense", "x")
    assert e.code == "internal" and e.status == 500


def test_code_for_status():
    assert errors.code_for_status(404) == "not_found"
    assert errors.code_for_status(422) == "invalid_request"
    assert errors.code_for_status(418) == "internal"


def test_envelope_shape():
    env = errors.envelope("not_found", "nope", False, "abc123")
    assert env == {"error": {"code": "not_found", "message": "nope", "retryable": False, "request_id": "abc123"}}


# ---- end-to-end via the app -------------------------------------------------

def test_not_found_renders_envelope():
    r = client.get("/chunk/chk_999999")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found" and err["retryable"] is False
    assert err["request_id"]
    assert r.headers["X-Request-ID"] == err["request_id"]


def test_malformed_handle_is_invalid_request():
    r = client.get("/chunk/@@@")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_missing_required_param_is_invalid_request():
    r = client.get("/search")  # q is required
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_bad_fields_is_invalid_request():
    # _resolve_fields validates before any retrieval work, so no model load
    r = client.get("/search", params={"q": "x", "fields": "bogus"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_request_id_header_on_success():
    r = client.get("/health")
    assert r.status_code == 200 and r.headers.get("X-Request-ID")
