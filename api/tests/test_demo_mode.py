"""The keyless playground: open by default, metered per visitor when public."""

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app


class _Req:
    def __init__(self, headers=None, host="1.2.3.4"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": host})()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("MOO_API_KEYS", raising=False)
    monkeypatch.delenv("MOO_DEMO_RATE_LIMIT_PER_MIN", raising=False)
    auth.reset()
    yield
    auth.reset()


def test_keyless_is_unlimited_without_a_demo_limit():
    assert auth.demo_limit() is None
    assert auth.active() is False
    for _ in range(100):
        assert auth.check(_Req()) == "local"


def test_a_demo_limit_meters_each_visitor_separately(monkeypatch):
    monkeypatch.setenv("MOO_DEMO_RATE_LIMIT_PER_MIN", "2")
    assert auth.active() is True

    assert auth.check(_Req(host="1.2.3.4")) == "ip:1.2.3.4"
    assert auth.check(_Req(host="1.2.3.4")) == "ip:1.2.3.4"
    with pytest.raises(auth.ApiError) as exc:
        auth.check(_Req(host="1.2.3.4"))
    assert exc.value.code == "rate_limited"
    assert exc.value.retry_after >= 1

    assert auth.check(_Req(host="5.6.7.8")) == "ip:5.6.7.8", "another visitor is unaffected"


def test_the_visitor_is_read_from_the_proxy_header(monkeypatch):
    monkeypatch.setenv("MOO_DEMO_RATE_LIMIT_PER_MIN", "5")
    identity = auth.check(_Req({"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, host="10.0.0.1"))
    assert identity == "ip:9.9.9.9"


def test_a_key_still_beats_the_demo_bucket(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "realkey")
    monkeypatch.setenv("MOO_DEMO_RATE_LIMIT_PER_MIN", "1")
    assert auth.check(_Req({"x-api-key": "realkey"})) == "realkey"
    assert auth.check(_Req({"x-api-key": "realkey"})) == "realkey"


def test_a_bad_key_is_still_rejected_in_demo_mode(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "realkey")
    monkeypatch.setenv("MOO_DEMO_RATE_LIMIT_PER_MIN", "5")
    with pytest.raises(auth.ApiError) as exc:
        auth.check(_Req({"x-api-key": "wrong"}))
    assert exc.value.code == "unauthorized"


def test_a_nonsense_limit_is_ignored(monkeypatch):
    for value in ("", "0", "-3", "lots"):
        monkeypatch.setenv("MOO_DEMO_RATE_LIMIT_PER_MIN", value)
        assert auth.demo_limit() is None


def test_the_playground_endpoint_returns_429_over_the_limit(monkeypatch):
    monkeypatch.setenv("MOO_DEMO_RATE_LIMIT_PER_MIN", "1")
    client = TestClient(app)
    assert client.get("/usage").status_code == 200
    response = client.get("/usage")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in response.headers
