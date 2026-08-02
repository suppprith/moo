"""Self-serve signup: GitHub sign-in to a key with free credits.

Nobody emails a founder for an API key. This is the whole flow: sign in with
GitHub, get a key shown once, start calling. It is off unless
``MOO_GITHUB_CLIENT_ID`` and ``MOO_GITHUB_CLIENT_SECRET`` are set, because an
open key-minting endpoint on a public instance is an abuse vector, and a
self-hosted moo needs no signup at all.

GitHub is the identity provider and nothing more: moo stores the account id,
login and public email, asks for no scopes, and never holds a GitHub token past
the exchange. Returning users get their existing account and a fresh key rather
than a duplicate account.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
from urllib.parse import urlencode

import httpx

from . import keys as key_store
from .credits import free_credits
from .errors import ApiError

log = logging.getLogger("moo.accounts")

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
STATE_COOKIE = "moo_oauth_state"
TIMEOUT = 15.0


def client_id() -> str | None:
    return os.environ.get("MOO_GITHUB_CLIENT_ID") or None


def _client_secret() -> str | None:
    return os.environ.get("MOO_GITHUB_CLIENT_SECRET") or None


def enabled() -> bool:
    return bool(client_id() and _client_secret())


def public_base_url() -> str:
    return os.environ.get("MOO_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")


def _http() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT)


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(state: str) -> str:
    """Where to send the browser. No scopes: moo only needs to know who you are."""
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": client_id(),
        "redirect_uri": f"{public_base_url()}/signup/github/callback",
        "state": state,
        "scope": "",
    })


def exchange_code(code: str, *, client: httpx.Client | None = None) -> dict:
    """Trade the callback code for the GitHub profile. Raises ApiError on any
    failure so the caller renders one shape."""
    http = client or _http()
    try:
        token_resp = http.post(
            os.environ.get("MOO_GITHUB_TOKEN_URL", TOKEN_URL),
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id(),
                "client_secret": _client_secret(),
                "code": code,
                "redirect_uri": f"{public_base_url()}/signup/github/callback",
            },
        )
        token_resp.raise_for_status()
        token = (token_resp.json() or {}).get("access_token")
        if not token:
            raise ApiError("upstream_error", "GitHub did not return an access token")
        user_resp = http.get(
            os.environ.get("MOO_GITHUB_USER_URL", USER_URL),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        user_resp.raise_for_status()
        profile = user_resp.json() or {}
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("github exchange failed: %s", exc)
        raise ApiError("upstream_error", "could not complete GitHub sign-in") from exc
    finally:
        if client is None:
            http.close()

    if not profile.get("id"):
        raise ApiError("upstream_error", "GitHub profile had no id")
    return profile


def upsert_account(conn: sqlite3.Connection, profile: dict, *, provider: str = "github") -> dict:
    """One account per provider identity; signing in again returns the same row."""
    provider_id = str(profile["id"])
    conn.execute(
        "INSERT INTO account (provider, provider_id, login, email) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(provider, provider_id) DO UPDATE SET "
        "login = excluded.login, email = COALESCE(excluded.email, account.email)",
        (provider, provider_id, profile.get("login"), profile.get("email")),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM account WHERE provider = ? AND provider_id = ?", (provider, provider_id)
    ).fetchone()
    return {k: row[k] for k in row.keys()}


def issue_key(conn: sqlite3.Connection, account: dict, *,
              credits: int | None = None) -> tuple[str, dict]:
    """Give an account a key on the free tier."""
    label = f"{account['provider']}:{account['login'] or account['provider_id']}"
    return key_store.create_key(
        conn, label=label, account_id=account["id"],
        credits_included=free_credits() if credits is None else credits,
    )


def keys_for_account(conn: sqlite3.Connection, account_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM api_key WHERE account_id = ? AND revoked_at IS NULL ORDER BY id",
        (account_id,),
    ).fetchall()
    return [{field: row[field] for field in key_store.ROW_FIELDS} for row in rows]
