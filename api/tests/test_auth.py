"""Optional API-key auth + rate limiting (app.auth, SUP-108)."""

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("MOO_API_KEYS", raising=False)
    monkeypatch.delenv("MOO_RATE_LIMIT_PER_MIN", raising=False)
    auth.reset()
    yield
    auth.reset()


# ---- unit -------------------------------------------------------------------

def test_disabled_by_default():
    assert auth.enabled() is False
    assert auth.check(_Req()) == "local"


def test_missing_or_wrong_key_rejected(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "secret1,secret2")
    with pytest.raises(auth.ApiError) as e1:
        auth.check(_Req())
    assert e1.value.code == "unauthorized"
    with pytest.raises(auth.ApiError):
        auth.check(_Req({"x-api-key": "nope"}))


def test_valid_key_accepted_via_both_header_forms(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "secret1")
    assert auth.check(_Req({"x-api-key": "secret1"})) == "secret1"
    assert auth.check(_Req({"authorization": "Bearer secret1"})) == "secret1"


def test_rate_limit_raises_with_retry_after(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "k")
    monkeypatch.setenv("MOO_RATE_LIMIT_PER_MIN", "3")
    for _ in range(3):
        auth.check(_Req({"x-api-key": "k"}))
    with pytest.raises(auth.ApiError) as e:
        auth.check(_Req({"x-api-key": "k"}))
    assert e.value.code == "rate_limited" and e.value.retry_after >= 1


def test_usage_metering_is_masked(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "supersecret")
    auth.check(_Req({"x-api-key": "supersecret"}))
    u = auth.usage()
    assert u == {"supe…": 1}
    assert "supersecret" not in str(u)


# ---- end to end via the app -------------------------------------------------

def test_health_open_even_with_auth_on(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "k")
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["auth"] is True


def test_gated_endpoint_401_without_key(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "k")
    client = TestClient(app)
    r = client.get("/usage")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_gated_endpoint_ok_with_key(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "k")
    client = TestClient(app)
    r = client.get("/usage", headers={"x-api-key": "k"})
    assert r.status_code == 200 and r.json()["enabled"] is True


def test_rate_limit_429_with_retry_after_header(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "k")
    monkeypatch.setenv("MOO_RATE_LIMIT_PER_MIN", "2")
    client = TestClient(app)
    h = {"x-api-key": "k"}
    assert client.get("/usage", headers=h).status_code == 200
    assert client.get("/usage", headers=h).status_code == 200
    r = client.get("/usage", headers=h)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in r.headers


def test_open_mode_needs_no_key():
    client = TestClient(app)  # no MOO_API_KEYS
    assert client.get("/usage").status_code == 200
