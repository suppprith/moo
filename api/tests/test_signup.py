"""Self-serve signup: GitHub sign-in to a working key, and the usage dashboard."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import accounts, auth, credits, keys
from app.db import get_connection, migrate
from app.main import app


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "signup.sqlite"
    migrate(path)
    monkeypatch.setattr(auth, "_connect", lambda: get_connection(path))
    monkeypatch.setattr("app.main.get_connection", lambda *a, **k: get_connection(path))
    auth.reset()
    yield path
    auth.reset()


@pytest.fixture
def db(db_path):
    conn = get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def oauth(monkeypatch):
    monkeypatch.setenv("MOO_GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("MOO_GITHUB_CLIENT_SECRET", "client-secret")
    # The public URL has to match the test client's host, or the state cookie
    # comes back on a different origin and never rides along.
    monkeypatch.setenv("MOO_PUBLIC_URL", "http://testserver")


PROFILE = {"id": 4242, "login": "alice", "email": "alice@example.com"}


def github_stub(profile=PROFILE, *, token="gho_token"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_token"):
            return httpx.Response(200, json={"access_token": token})
        return httpx.Response(200, json=profile)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def stub_github(monkeypatch):
    monkeypatch.setattr(accounts, "_http", github_stub)


def test_signup_page_explains_manual_keys_when_oauth_is_off(monkeypatch):
    monkeypatch.delenv("MOO_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("MOO_GITHUB_CLIENT_SECRET", raising=False)
    body = TestClient(app).get("/signup").text
    assert "app.keys create" in body
    assert "MOO_GITHUB_CLIENT_ID" in body


def test_signup_page_offers_github_when_configured(oauth):
    body = TestClient(app).get("/signup").text
    assert "/signup/github" in body
    assert "1000 credits" in body


def test_signup_redirects_to_github_with_a_state_cookie(oauth):
    client = TestClient(app)
    response = client.get("/signup/github", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize")
    assert "client_id=client-id" in location
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fsignup%2Fgithub%2Fcallback" in location
    assert client.cookies.get(accounts.STATE_COOKIE)


def test_the_state_cookie_is_secure_behind_https(oauth, monkeypatch):
    monkeypatch.setenv("MOO_PUBLIC_URL", "https://api.moo.test")
    response = TestClient(app).get("/signup/github", follow_redirects=False)
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_signup_is_404_when_not_configured(monkeypatch):
    monkeypatch.delenv("MOO_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("MOO_GITHUB_CLIENT_SECRET", raising=False)
    assert TestClient(app).get("/signup/github").status_code == 404


def test_callback_without_the_state_cookie_is_rejected(oauth, db_path, stub_github):
    response = TestClient(app).get("/signup/github/callback?code=abc&state=forged")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_callback_issues_a_working_key_shown_once(oauth, db, stub_github):
    client = TestClient(app)
    client.get("/signup/github", follow_redirects=False)
    state = client.cookies.get(accounts.STATE_COOKIE)

    page = client.get(f"/signup/github/callback?code=abc&state={state}")
    assert page.status_code == 200
    assert "alice" in page.text

    issued = [word for word in page.text.replace("<", " ").replace(">", " ").split()
              if word.startswith("moo_sk_")]
    assert issued, "the key is shown exactly once, on this page"
    key = issued[0]

    row = keys.lookup(db, key)
    assert row is not None
    assert row["credits_included"] == 1000
    assert row["label"] == "github:alice"
    stored = db.execute("SELECT key_hash FROM api_key").fetchone()["key_hash"]
    assert stored == keys.key_hash(key) and stored != key


def test_signing_in_again_reuses_the_account(oauth, db, stub_github):
    client = TestClient(app)
    for _ in range(2):
        client.get("/signup/github", follow_redirects=False)
        state = client.cookies.get(accounts.STATE_COOKIE)
        assert client.get(f"/signup/github/callback?code=abc&state={state}").status_code == 200

    accounts_rows = db.execute("SELECT * FROM account").fetchall()
    assert len(accounts_rows) == 1
    assert len(accounts.keys_for_account(db, accounts_rows[0]["id"])) == 2, "a fresh key each time"


def test_github_failure_surfaces_as_an_envelope(oauth, db_path, monkeypatch):
    def failing():
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(500, json={"message": "boom"})))

    monkeypatch.setattr(accounts, "_http", failing)
    client = TestClient(app)
    client.get("/signup/github", follow_redirects=False)
    state = client.cookies.get(accounts.STATE_COOKIE)
    response = client.get(f"/signup/github/callback?code=abc&state={state}")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


def test_usage_needs_the_key_itself(db):
    key, _ = keys.create_key(db, label="alice", credits_included=1000)
    client = TestClient(app)

    assert client.get("/account/usage").status_code == 401
    assert client.get("/account/usage", headers={"x-api-key": "moo_sk_wrong"}).status_code == 401

    body = client.get("/account/usage", headers={"authorization": f"Bearer {key}"}).json()
    assert body["key"]["label"] == "alice"
    assert body["credits"]["included"] == 1000
    assert body["credits"]["remaining"] == 1000


def test_usage_reflects_what_was_spent(db):
    key, row = keys.create_key(db, credits_included=1000)
    credits.charge(db, row["id"], "/v1/web_search", 1)
    credits.charge(db, row["id"], "/research", 10)
    body = TestClient(app).get("/account/usage",
                               headers={"x-api-key": key}).json()
    assert body["credits"]["used"] == 11
    assert body["credits"]["remaining"] == 989
    assert [e["endpoint"] for e in body["usage"]["endpoints"]] == ["/research", "/v1/web_search"]


def test_dashboard_never_puts_the_key_in_a_url_or_storage():
    body = TestClient(app).get("/account").text
    assert "localStorage" not in body
    assert "type=\"password\"" in body
    assert "authorization" in body


def test_a_call_charges_the_key_that_made_it(db, monkeypatch):
    key, row = keys.create_key(db, credits_included=1000)
    client = TestClient(app)

    assert client.get("/usage", headers={"x-api-key": key}).status_code == 200
    assert keys.list_keys(db)[0]["credits_used"] == 0, "introspection is free"

    charged = []
    monkeypatch.setattr(auth, "charge", lambda key_id, endpoint, units:
                        charged.append((key_id, endpoint, units)))
    monkeypatch.setattr("app.main.search", lambda *a, **k: {"query": "q", "mode": "raw",
                                                            "intent": "definition", "sources": [],
                                                            "claims": [], "graph": {},
                                                            "citations": [], "meta": {}})
    monkeypatch.setattr("app.main.get_connection_for_search", lambda: get_connection(":memory:"))
    assert client.get("/search?q=wal", headers={"x-api-key": key}).status_code == 200
    assert charged == [(row["id"], "/search", 1)]


def test_an_extract_call_is_charged_per_url(db, monkeypatch):
    key, row = keys.create_key(db, credits_included=1000)
    charged = []
    monkeypatch.setattr(auth, "charge", lambda key_id, endpoint, units:
                        charged.append((key_id, endpoint, units)))
    monkeypatch.setattr("app.main.extract_urls", lambda *a, **k: {"results": []})
    monkeypatch.setattr("app.main.get_connection_for_search", lambda: get_connection(":memory:"))
    response = TestClient(app).post(
        "/v1/extract", headers={"x-api-key": key},
        json={"urls": ["https://a.dev", "https://b.dev", "https://c.dev"]})
    assert response.status_code == 200
    assert charged == [(row["id"], "/v1/extract", 3)]


def test_a_failed_call_is_not_billed(db, monkeypatch):
    key, _ = keys.create_key(db, credits_included=1000)
    charged = []
    monkeypatch.setattr(auth, "charge", lambda *a: charged.append(a))

    def explode(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr("app.main.search", explode)
    monkeypatch.setattr("app.main.get_connection_for_search", lambda: get_connection(":memory:"))
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/search?q=wal", headers={"x-api-key": key}).status_code == 500
    assert charged == []
