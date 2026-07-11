-- SUP-112: persist deep-research runs so they are inspectable, resumable, and
-- purgeable. Claims/evidence themselves live in the shared graph (claim,
-- evidence, claim_chunk); these tables associate a run with the work it did.

CREATE TABLE research_run (
    id          TEXT    PRIMARY KEY,               -- uuid hex; the public run handle
    question    TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'running', -- planning | running | done | partial | failed
    intent      TEXT,
    plan        TEXT,                               -- JSON: the SUP-110 plan
    coverage    TEXT,                               -- JSON: per-sub-question coverage (final)
    budget      TEXT,                               -- JSON: {max_steps, steps_used, exhausted, ...}
    generator   TEXT,                               -- model | heuristic
    error       TEXT,                               -- set when status = failed
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE research_step (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT    NOT NULL REFERENCES research_run(id) ON DELETE CASCADE,
    step_no         INTEGER NOT NULL,
    sub_question_id INTEGER,
    query           TEXT    NOT NULL,
    reason          TEXT    NOT NULL,               -- plan | gap | contradiction
    claims          INTEGER NOT NULL DEFAULT 0,
    new_claims      INTEGER NOT NULL DEFAULT 0,
    disputed        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (run_id, step_no)
);

-- accumulated claims for a run (the claims live in `claim`; this is the join)
CREATE TABLE research_claim (
    run_id          TEXT    NOT NULL REFERENCES research_run(id) ON DELETE CASCADE,
    claim_id        INTEGER NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
    sub_question_id INTEGER,
    PRIMARY KEY (run_id, claim_id)
);

CREATE INDEX idx_research_step_run  ON research_step (run_id);
CREATE INDEX idx_research_claim_run ON research_claim (run_id);
