"""Issued API keys: create, look up, revoke, meter.

Keys are shown once at creation and stored only as a sha256 hash, so the
database never holds anything usable. Each key carries an optional per-key rate
limit and request quota, plus counters that survive restarts, which is what
``MOO_API_KEYS`` (env, all-or-nothing, no metering) cannot do.

    uv run python -m app.keys create --label "alice" --quota 1000
    uv run python -m app.keys list
    uv run python -m app.keys revoke moo_sk_a1b2
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import sqlite3

KEY_PREFIX = "moo_sk_"
PREFIX_CHARS = len(KEY_PREFIX) + 6
ROW_FIELDS = ("id", "prefix", "label", "rate_limit_per_min", "quota", "requests",
              "created_at", "last_used_at", "revoked_at")


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _row(row: sqlite3.Row | None) -> dict | None:
    return {field: row[field] for field in ROW_FIELDS} if row is not None else None


def create_key(
    conn: sqlite3.Connection,
    *,
    label: str | None = None,
    rate_limit_per_min: int | None = None,
    quota: int | None = None,
) -> tuple[str, dict]:
    """Issue a key. Returns the plaintext key, which is never recoverable after
    this call, alongside its row."""
    key = generate_key()
    cursor = conn.execute(
        "INSERT INTO api_key (key_hash, prefix, label, rate_limit_per_min, quota) "
        "VALUES (?, ?, ?, ?, ?)",
        (key_hash(key), key[:PREFIX_CHARS], label, rate_limit_per_min, quota),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM api_key WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return key, _row(row)


def lookup(conn: sqlite3.Connection, key: str) -> dict | None:
    """The active row for a presented key, or None (unknown, revoked, or the
    table does not exist yet on a database predating the migration)."""
    try:
        row = conn.execute(
            "SELECT * FROM api_key WHERE key_hash = ? AND revoked_at IS NULL", (key_hash(key),)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return _row(row)


def record_use(conn: sqlite3.Connection, key_id: int) -> None:
    """Count one request against a key. No query content is ever stored."""
    conn.execute(
        "UPDATE api_key SET requests = requests + 1, last_used_at = datetime('now') WHERE id = ?",
        (key_id,),
    )
    conn.commit()


def count(conn: sqlite3.Connection, *, active_only: bool = False) -> int:
    """How many keys this instance has issued. The total (not the active count)
    is what decides whether auth is on: revoking the last key must not silently
    reopen a gated instance. Delete the rows to go back to open."""
    sql = "SELECT COUNT(*) AS n FROM api_key"
    if active_only:
        sql += " WHERE revoked_at IS NULL"
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["n"])


def list_keys(conn: sqlite3.Connection, *, include_revoked: bool = False) -> list[dict]:
    sql = "SELECT * FROM api_key"
    if not include_revoked:
        sql += " WHERE revoked_at IS NULL"
    return [_row(row) for row in conn.execute(sql + " ORDER BY id")]


def revoke(conn: sqlite3.Connection, identifier: str) -> dict | None:
    """Revoke by prefix or numeric id. A revoked key stays in the table so its
    usage history survives."""
    column = "id" if identifier.isdigit() else "prefix"
    row = conn.execute(
        f"SELECT * FROM api_key WHERE {column} = ? AND revoked_at IS NULL", (identifier,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE api_key SET revoked_at = datetime('now') WHERE id = ?", (row["id"],))
    conn.commit()
    return _row(conn.execute("SELECT * FROM api_key WHERE id = ?", (row["id"],)).fetchone())


def main(argv: list[str] | None = None) -> int:
    import os

    from . import db as db_module
    from .db import get_connection, migrate

    parser = argparse.ArgumentParser(description="manage moo API keys")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="issue a new key (shown once)")
    create.add_argument("--label", help="who or what this key is for")
    create.add_argument("--rate-limit", type=int, help="per-minute limit for this key")
    create.add_argument("--quota", type=int, help="total requests allowed")

    listing = sub.add_parser("list", help="list keys")
    listing.add_argument("--all", action="store_true", help="include revoked keys")

    revoking = sub.add_parser("revoke", help="revoke a key by prefix or id")
    revoking.add_argument("identifier")

    args = parser.parse_args(argv)
    db_path = os.environ.get("MOO_DB_PATH") or db_module.DEFAULT_DB_PATH
    migrate(db_path)
    conn = get_connection(db_path)
    try:
        if args.command == "create":
            key, row = create_key(conn, label=args.label, rate_limit_per_min=args.rate_limit,
                                  quota=args.quota)
            print(key)
            print(f"prefix {row['prefix']}  label {row['label'] or '-'}  "
                  f"quota {row['quota'] or 'unmetered'}")
            print("Store it now. Only its hash is kept.")
        elif args.command == "list":
            rows = list_keys(conn, include_revoked=args.all)
            if not rows:
                print("no keys; auth is open unless MOO_API_KEYS is set")
            for row in rows:
                state = "revoked" if row["revoked_at"] else "active"
                print(f"{row['prefix']}  {state}  requests {row['requests']}"
                      f"{'/' + str(row['quota']) if row['quota'] else ''}  "
                      f"label {row['label'] or '-'}  last used {row['last_used_at'] or 'never'}")
        else:
            row = revoke(conn, args.identifier)
            print(f"revoked {row['prefix']}" if row else f"no active key {args.identifier!r}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
