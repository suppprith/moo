"""Credits: pricing, the rolling free tier, and per-endpoint usage."""

from datetime import UTC, datetime, timedelta

import pytest

from app import auth, credits, keys
from app.db import get_connection, migrate


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "credits.sqlite"
    migrate(path)
    return path


@pytest.fixture
def db(db_path):
    conn = get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def keyed(db_path, monkeypatch):
    monkeypatch.delenv("MOO_API_KEYS", raising=False)
    monkeypatch.delenv("MOO_RATE_LIMIT_PER_MIN", raising=False)
    monkeypatch.setattr(auth, "_connect", lambda: get_connection(db_path))
    auth.reset()
    yield
    auth.reset()


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


def _backdate(conn, key_id, days):
    when = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute("UPDATE api_key SET period_start = ? WHERE id = ?", (when, key_id))
    conn.commit()


def test_a_fast_search_costs_one_credit():
    assert credits.cost_for_path("/search") == 1
    assert credits.cost_for_path("/v1/web_search") == 1
    assert credits.cost_for_path("/search/stream") == 1


def test_following_a_handle_is_free():
    """Charging to chase a citation would tax the reason moo exists."""
    for path in ("/source/doc_1", "/chunk/chk_7", "/claim/clm_2", "/graph", "/graph/expand",
                 "/research/abc123"):
        assert credits.cost_for_path(path) == 0


def test_introspection_and_account_pages_are_free():
    for path in ("/health", "/contract", "/v1/tools", "/usage", "/account/usage", "/signup"):
        assert credits.cost_for_path(path) == 0


def test_deep_research_is_priced_by_the_work_it_may_do():
    assert credits.research_cost(None) == 10
    assert credits.research_cost(6) == 10
    assert credits.research_cost(7) == 15
    assert credits.research_cost(20) == 25, "cost is capped"


def test_charging_records_the_endpoint_and_deducts(db):
    _, row = keys.create_key(db, credits_included=100)
    credits.charge(db, row["id"], "/v1/web_search", 1)
    credits.charge(db, row["id"], "/v1/web_search", 1)
    credits.charge(db, row["id"], "/research", 10)
    fresh = keys.list_keys(db)[0]
    assert fresh["credits_used"] == 12
    breakdown = credits.usage_breakdown(db, row["id"])
    assert breakdown["endpoints"][0] == {"endpoint": "/research", "requests": 1, "credits": 10}
    assert {e["endpoint"]: e["requests"] for e in breakdown["endpoints"]}["/v1/web_search"] == 2
    assert len(breakdown["daily"]) == 1


def test_remaining_and_exhaustion(db):
    _, row = keys.create_key(db, credits_included=3)
    assert credits.remaining(row) == 3
    credits.charge(db, row["id"], "/search", 3)
    fresh = keys.list_keys(db)[0]
    assert credits.remaining(fresh) == 0
    assert credits.exhausted(fresh) is True


def test_an_unmetered_key_never_runs_out(db):
    _, row = keys.create_key(db)
    assert row["credits_included"] is None
    assert credits.remaining(row) is None
    assert credits.exhausted(row) is False


def test_the_window_rolls_lazily(db):
    _, row = keys.create_key(db, credits_included=10)
    credits.charge(db, row["id"], "/search", 8)
    used = keys.list_keys(db)[0]
    assert credits.roll_period(db, used)["credits_used"] == 8, "inside the window, nothing resets"

    _backdate(db, row["id"], 31)
    rolled = credits.roll_period(db, keys.list_keys(db)[0])
    assert rolled["credits_used"] == 0
    assert credits.remaining(rolled) == 10


def test_period_end_is_thirty_days_out(db):
    _, row = keys.create_key(db, credits_included=10)
    end = credits.period_end(row["period_start"])
    assert end and end > row["period_start"]


def test_exhausted_credits_are_rejected_as_budget_not_rate_limit(db, keyed):
    key, row = keys.create_key(db, credits_included=2)
    credits.charge(db, row["id"], "/search", 2)
    with pytest.raises(auth.ApiError) as exc:
        auth.check(_Req({"x-api-key": key}))
    assert exc.value.code == "budget_exceeded"
    assert exc.value.retryable is False
    assert "reset" in exc.value.message


def test_a_rolled_window_lets_a_key_back_in(db, keyed):
    key, row = keys.create_key(db, credits_included=1)
    credits.charge(db, row["id"], "/search", 1)
    with pytest.raises(auth.ApiError):
        auth.check(_Req({"x-api-key": key}))
    _backdate(db, row["id"], 31)
    assert auth.check(_Req({"x-api-key": key})) == row["prefix"]


def test_authenticate_reports_the_key_to_charge(db, keyed):
    key, row = keys.create_key(db, credits_included=100)
    identity = auth.authenticate(_Req({"x-api-key": key}))
    assert identity["key_id"] == row["id"]
    assert identity["identity"] == row["prefix"]


def test_env_keys_have_no_credits_to_spend(keyed, monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "envkey")
    identity = auth.authenticate(_Req({"x-api-key": "envkey"}))
    assert identity["key_id"] is None
    auth.charge(identity["key_id"], "/search", 1)


def test_account_view_is_dashboard_shaped(db):
    _, row = keys.create_key(db, label="alice", credits_included=1000)
    credits.charge(db, row["id"], "/v1/web_search", 4)
    view = credits.account_view(db, keys.list_keys(db)[0])
    assert view["key"]["label"] == "alice"
    assert view["credits"] == {
        "included": 1000, "used": 4, "remaining": 996,
        "period_start": row["period_start"], "period_end": credits.period_end(row["period_start"]),
        "unmetered": False,
    }
    assert view["usage"]["endpoints"][0]["endpoint"] == "/v1/web_search"
    assert view["prices"]["drill_down"] == 0
    assert "key_hash" not in str(view)


def test_free_tier_size_is_configurable(monkeypatch):
    monkeypatch.delenv("MOO_FREE_CREDITS", raising=False)
    assert credits.free_credits() == 1000
    monkeypatch.setenv("MOO_FREE_CREDITS", "250")
    assert credits.free_credits() == 250
    monkeypatch.setenv("MOO_FREE_CREDITS", "nonsense")
    assert credits.free_credits() == 1000
