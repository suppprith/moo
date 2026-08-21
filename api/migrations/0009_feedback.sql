-- Opt-in usefulness signal (see app/feedback.py).
--
-- Counts only. There is deliberately no row per event, no timestamp finer than
-- a date, no key id, no session and no query text: the table cannot reconstruct
-- who asked what, or in what order, because it never holds those things. What
-- it holds is "this source was cited N times", which is the whole input the
-- ranker needs.

CREATE TABLE feedback (
    kind      TEXT    NOT NULL CHECK (kind IN ('chk', 'doc')),
    target_id INTEGER NOT NULL,
    signal    TEXT    NOT NULL CHECK (signal IN ('cited', 'fetched', 'helpful', 'unhelpful')),
    count     INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT    NOT NULL DEFAULT (date('now')),
    PRIMARY KEY (kind, target_id, signal)
);

CREATE INDEX idx_feedback_target ON feedback (kind, target_id);
