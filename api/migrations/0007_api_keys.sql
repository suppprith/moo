-- Persistent API keys.
--
-- Keys were a comma-separated env var, which cannot be issued, revoked or
-- metered without a redeploy. This table is what a hosted instance needs:
-- one row per key, hashed at rest (a leaked database does not leak usable
-- keys), with an optional per-key rate limit and request quota, and counters
-- that survive a restart. The env var still works and takes precedence, so
-- local and self-hosted setups are unchanged.

CREATE TABLE api_key (
    id                 INTEGER PRIMARY KEY,
    key_hash           TEXT    NOT NULL UNIQUE,  -- sha256 of the key; the key itself is never stored
    prefix             TEXT    NOT NULL,         -- leading chars, to identify a key in listings
    label              TEXT,                     -- who or what it was issued for
    rate_limit_per_min INTEGER,                  -- NULL = server default
    quota              INTEGER,                  -- NULL = unmetered; total requests allowed
    requests           INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    last_used_at       TEXT,
    revoked_at         TEXT
);

CREATE INDEX idx_api_key_active ON api_key(key_hash) WHERE revoked_at IS NULL;
