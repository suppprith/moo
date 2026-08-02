import httpx
import pytest

import moo.client as client_mod
from moo import (
    ConnectionError as MooConnectionError,
)
from moo import (
    InvalidRequestError,
    Moo,
    MooError,
    NotFoundError,
    RateLimitError,
)


def envelope(code, message, retryable=False, request_id="rid-1"):
    return {"error": {"code": code, "message": message, "retryable": retryable,
                      "request_id": request_id}}


def client(handler, **kwargs):
    return Moo(base_url="https://moo.test",
               http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_not_found_maps_to_typed_error():
    def handler(request):
        return httpx.Response(404, json=envelope("not_found", "no chunk 'chk_9'"))

    with pytest.raises(NotFoundError) as exc:
        client(handler).chunk("chk_9")
    assert exc.value.code == "not_found"
    assert exc.value.request_id == "rid-1"
    assert exc.value.status == 404
    assert exc.value.retryable is False
    assert "chk_9" in str(exc.value)


def test_validation_error_maps_to_invalid_request():
    def handler(request):
        return httpx.Response(422, json=envelope("invalid_request", "mode: bad value"))

    with pytest.raises(InvalidRequestError):
        client(handler).search("q", mode="nope")


def test_non_envelope_body_falls_back_to_status():
    def handler(request):
        return httpx.Response(502, content=b"<html>bad gateway</html>")

    with pytest.raises(MooError) as exc:
        client(handler, max_retries=0).health()
    assert exc.value.code == "upstream_error"
    assert exc.value.retryable is True


def test_rate_limit_is_retried_honoring_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr(client_mod.time, "sleep", slept.append)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json=envelope("rate_limited", "slow down", True),
                                  headers={"Retry-After": "3"})
        return httpx.Response(200, json={"status": "ok"})

    assert client(handler).health() == {"status": "ok"}
    assert calls["n"] == 2
    assert slept == [3.0]


def test_retries_are_bounded_then_raise(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json=envelope("rate_limited", "slow down", True),
                              headers={"Retry-After": "1"})

    with pytest.raises(RateLimitError) as exc:
        client(handler, max_retries=2).health()
    assert calls["n"] == 3
    assert exc.value.retry_after == 1


def test_client_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, json=envelope("not_found", "gone"))

    with pytest.raises(NotFoundError):
        client(handler).chunk("chk_1")
    assert calls["n"] == 1


def test_unreachable_server_raises_connection_error(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)

    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(MooConnectionError) as exc:
        client(handler, max_retries=1).health()
    assert exc.value.retryable is True
