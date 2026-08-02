"""Issued API keys: hashing, quotas, revocation, metering."""

import pytest

from app import auth, keys
from app.db import get_connection, migrate


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "keys.sqlite"
    migrate(path)
    return path


@pytest.fixture
def db(db_path):
    conn = get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def keyed(db_path, monkeypatch):
    """Point auth at the temp database and clear its caches."""
    monkeypatch.delenv("MOO_API_KEYS", raising=False)
    monkeypatch.delenv("MOO_RATE_LIMIT_PER_MIN", raising=False)
    monkeypatch.setattr(auth, "_connect", lambda: get_connection(db_path))
    auth.reset()
    yield
    auth.reset()


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_key_is_stored_only_as_a_hash(db):
    key, row = keys.create_key(db, label="alice")
    assert key.startswith("moo_sk_")
    stored = db.execute("SELECT key_hash FROM api_key").fetchone()["key_hash"]
    assert stored == keys.key_hash(key)
    assert key not in str(dict(db.execute("SELECT * FROM api_key").fetchone()))
    assert row["prefix"] == key[:13] and row["label"] == "alice"


def test_lookup_matches_only_the_exact_key(db):
    key, _ = keys.create_key(db)
    assert keys.lookup(db, key)["label"] is None
    assert keys.lookup(db, key + "x") is None


def test_revoked_key_stops_resolving_but_keeps_history(db):
    key, row = keys.create_key(db, label="bob")
    keys.record_use(db, row["id"])
    assert keys.revoke(db, row["prefix"])["revoked_at"] is not None
    assert keys.lookup(db, key) is None
    assert keys.list_keys(db) == []
    kept = keys.list_keys(db, include_revoked=True)
    assert len(kept) == 1 and kept[0]["requests"] == 1


def test_revoking_an_unknown_key_is_reported(db):
    assert keys.revoke(db, "moo_sk_nope") is None


def test_record_use_counts_and_timestamps(db):
    _, row = keys.create_key(db)
    assert row["requests"] == 0 and row["last_used_at"] is None
    keys.record_use(db, row["id"])
    keys.record_use(db, row["id"])
    updated = keys.list_keys(db)[0]
    assert updated["requests"] == 2 and updated["last_used_at"] is not None


def test_auth_accepts_an_issued_key_and_meters_it(db, keyed):
    key, row = keys.create_key(db, label="alice")
    assert auth.enabled() is True
    assert auth.check(_Req({"authorization": f"Bearer {key}"})) == row["prefix"]
    assert keys.list_keys(db)[0]["requests"] == 1


def test_auth_rejects_a_revoked_key(db, keyed):
    key, row = keys.create_key(db)
    other, other_row = keys.create_key(db, label="still valid")
    auth.check(_Req({"x-api-key": key}))
    keys.revoke(db, row["prefix"])
    auth.reset()
    with pytest.raises(auth.ApiError) as exc:
        auth.check(_Req({"x-api-key": key}))
    assert exc.value.code == "unauthorized"
    assert auth.check(_Req({"x-api-key": other})) == other_row["prefix"]


def test_revoking_the_last_key_does_not_reopen_the_instance(db, keyed):
    key, row = keys.create_key(db)
    keys.revoke(db, row["prefix"])
    auth.reset()
    assert auth.enabled() is True
    with pytest.raises(auth.ApiError):
        auth.check(_Req({"x-api-key": key}))
    with pytest.raises(auth.ApiError):
        auth.check(_Req())


def test_quota_exhaustion_is_not_a_rate_limit(db, keyed):
    key, _ = keys.create_key(db, quota=2)
    auth.check(_Req({"x-api-key": key}))
    auth.check(_Req({"x-api-key": key}))
    with pytest.raises(auth.ApiError) as exc:
        auth.check(_Req({"x-api-key": key}))
    assert exc.value.code == "budget_exceeded"
    assert exc.value.status == 429


def test_per_key_rate_limit_overrides_the_default(db, keyed, monkeypatch):
    monkeypatch.setenv("MOO_RATE_LIMIT_PER_MIN", "60")
    key, _ = keys.create_key(db, rate_limit_per_min=1)
    auth.check(_Req({"x-api-key": key}))
    with pytest.raises(auth.ApiError) as exc:
        auth.check(_Req({"x-api-key": key}))
    assert exc.value.code == "rate_limited"


def test_env_keys_still_work_alongside_issued_keys(db, keyed, monkeypatch):
    monkeypatch.setenv("MOO_API_KEYS", "envkey")
    key, row = keys.create_key(db)
    assert auth.check(_Req({"x-api-key": "envkey"})) == "envkey"
    assert auth.check(_Req({"x-api-key": key})) == row["prefix"]
    with pytest.raises(auth.ApiError):
        auth.check(_Req({"x-api-key": "neither"}))


def test_issued_usage_reports_counters_without_the_key(db, keyed):
    key, _ = keys.create_key(db, label="alice", quota=5)
    auth.check(_Req({"x-api-key": key}))
    reported = auth.issued_usage()
    assert len(reported) == 1
    assert reported[0]["label"] == "alice"
    assert reported[0]["requests"] == 1
    assert reported[0]["quota"] == 5
    assert "key_hash" not in reported[0]
    assert key not in str(reported)


def test_cli_creates_lists_and_revokes(db_path, monkeypatch, capsys):
    monkeypatch.setenv("MOO_DB_PATH", str(db_path))
    keys.main(["create", "--label", "ci", "--quota", "10"])
    issued = capsys.readouterr().out.splitlines()[0]
    assert issued.startswith("moo_sk_")
    keys.main(["list"])
    assert "label ci" in capsys.readouterr().out
    keys.main(["revoke", issued[:13]])
    assert "revoked" in capsys.readouterr().out
