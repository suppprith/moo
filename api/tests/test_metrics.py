"""Per-route latency counters, rate-limit headers, and the ops endpoints."""

import pytest
from fastapi.testclient import TestClient

from app import auth, metrics
from app.main import app


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("MOO_API_KEYS", raising=False)
    monkeypatch.delenv("MOO_RATE_LIMIT_PER_MIN", raising=False)
    monkeypatch.delenv("MOO_DEMO_RATE_LIMIT_PER_MIN", raising=False)
    auth.reset()
    metrics.reset()
    yield
    auth.reset()
    metrics.reset()


def test_percentiles_over_recorded_samples():
    for ms in (10, 20, 30, 40, 500):
        metrics.record("GET", "/search", 200, ms)
    row = metrics.snapshot()["routes"]["GET /search"]
    assert row["requests"] == 5
    assert row["p50_ms"] == 30
    assert row["p95_ms"] == 500
    assert row["max_ms"] == 500
    assert row["2xx"] == 5


def test_status_classes_and_recent_errors():
    metrics.record("GET", "/search", 200, 1)
    metrics.record("GET", "/search", 422, 1)
    metrics.record("GET", "/search", 500, 1)
    snap = metrics.snapshot()
    assert snap["totals"] == {"requests": 3, "2xx": 1, "4xx": 1, "5xx": 1}
    assert snap["errors_5xx_recent"] == 1


def test_window_keeps_only_recent_samples():
    for i in range(metrics.WINDOW + 50):
        metrics.record("GET", "/health", 200, float(i))
    row = metrics.snapshot()["routes"]["GET /health"]
    assert row["requests"] == metrics.WINDOW + 50
    assert row["max_ms"] == float(metrics.WINDOW + 49)


def test_health_reports_uptime_and_error_count():
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert body["uptime_seconds"] >= 0
    assert body["errors_5xx_recent"] == 0


def test_metrics_endpoint_reports_the_route_it_was_asked_through():
    client = TestClient(app)
    client.get("/health")
    body = client.get("/metrics").json()
    assert body["routes"]["GET /health"]["requests"] == 1
    assert body["routes"]["GET /health"]["p50_ms"] >= 0
    assert "totals" in body and "uptime_seconds" in body


def test_rate_limit_headers_when_a_key_is_required(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "secret")
    monkeypatch.setenv("MOO_RATE_LIMIT_PER_MIN", "3")
    client = TestClient(app)
    first = client.get("/graph", params={"q": "postgres"}, headers={"x-api-key": "secret"})
    assert first.headers["X-RateLimit-Limit"] == "3"
    assert first.headers["X-RateLimit-Remaining"] == "2"
    assert int(first.headers["X-RateLimit-Reset"]) <= 60


def test_exhausted_limit_reports_zero_remaining(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "secret")
    monkeypatch.setenv("MOO_RATE_LIMIT_PER_MIN", "1")
    client = TestClient(app)
    client.get("/graph", params={"q": "postgres"}, headers={"x-api-key": "secret"})
    blocked = client.get("/graph", params={"q": "postgres"}, headers={"x-api-key": "secret"})
    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    assert blocked.headers["Retry-After"] == blocked.headers["X-RateLimit-Reset"]


def test_no_rate_headers_when_nothing_is_limited():
    response = TestClient(app).get("/health")
    assert "X-RateLimit-Limit" not in response.headers


def test_rejected_calls_are_still_counted(monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "secret")
    TestClient(app).get("/graph", params={"q": "x"})
    assert metrics.snapshot()["totals"]["4xx"] == 1
