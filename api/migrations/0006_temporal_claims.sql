-- Temporal / versioned evidence graph (SUP-137).
--
-- Claims gain version validity ("true as of Postgres 13, removed in 15"), and
-- a claim-to-claim `supersedes` edge records that a newer-version claim
-- replaces an older one. This is what lets moo distinguish a LIVE
-- contradiction (sources disagree today) from HISTORICAL supersession
-- (behavior changed across versions) — a superseded claim is not "disputed".

ALTER TABLE claim ADD COLUMN valid_product TEXT;   -- canonical product (versions.py)
ALTER TABLE claim ADD COLUMN valid_from    TEXT;   -- version string, inclusive
ALTER TABLE claim ADD COLUMN valid_until   TEXT;   -- version where deprecated/removed

-- claim -> claim typed edge (extensible beyond supersedes later)
CREATE TABLE claim_link (
    claim_id        INTEGER NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
    target_claim_id INTEGER NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
    relation        TEXT    NOT NULL CHECK (relation IN ('supersedes')),
    rationale       TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (claim_id, target_claim_id, relation),
    CHECK (claim_id != target_claim_id)
);

CREATE INDEX idx_claim_link_target ON claim_link (target_claim_id);
