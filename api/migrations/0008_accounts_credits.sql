-- Accounts, credits, and per-endpoint usage.
--
-- Keys existed but had to be issued by hand, and metering counted bare
-- requests, which cannot price a deep-research run against a snippet search.
-- An account owns keys, a key carries a monthly credit allowance that resets on
-- a rolling period, and usage is recorded per day per endpoint so a dashboard
-- can show where the credits went. Still no query content anywhere.

CREATE TABLE account (
    id          INTEGER PRIMARY KEY,
    provider    TEXT NOT NULL,             -- 'github' today
    provider_id TEXT NOT NULL,             -- stable id at the provider
    login       TEXT,
    email       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (provider, provider_id)
);

ALTER TABLE api_key ADD COLUMN account_id       INTEGER REFERENCES account(id);
ALTER TABLE api_key ADD COLUMN credits_included INTEGER;  -- NULL = unmetered
ALTER TABLE api_key ADD COLUMN credits_used     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE api_key ADD COLUMN period_start     TEXT;     -- start of the current window

CREATE INDEX idx_api_key_account ON api_key(account_id);

CREATE TABLE api_key_usage (
    key_id   INTEGER NOT NULL REFERENCES api_key(id) ON DELETE CASCADE,
    day      TEXT    NOT NULL,             -- YYYY-MM-DD, UTC
    endpoint TEXT    NOT NULL,             -- route template, never a query
    requests INTEGER NOT NULL DEFAULT 0,
    credits  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key_id, day, endpoint)
);
