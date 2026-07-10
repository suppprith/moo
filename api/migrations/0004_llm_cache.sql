-- SUP-82: cache for LLM pipeline stages (expansion now; claims/rerank later).
-- Keyed by stage + normalized input hash so repeat/demo queries cost zero LLM calls.

CREATE TABLE llm_cache (
    key        TEXT PRIMARY KEY,      -- "<stage>:<sha256 of normalized input>"
    value      TEXT NOT NULL,         -- JSON payload
    model      TEXT,                  -- model that produced it ("heuristic" for fallback)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
