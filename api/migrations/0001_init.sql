-- moo search — core data model (Phase 0, SUP-71)
-- Single SQLite file. FTS5 + sqlite-vec tables are added in Phase 2 (see docs/data-model.md).

-- ---------------------------------------------------------------------------
-- Sources
-- ---------------------------------------------------------------------------

CREATE TABLE document (
    id            INTEGER PRIMARY KEY,
    source_type   TEXT    NOT NULL,        -- github_issue | github_pr | docs | blog | so | hn | reddit
    url           TEXT    NOT NULL UNIQUE,
    title         TEXT,
    author        TEXT,
    author_role   TEXT,                    -- maintainer | member | contributor | none (feeds trust)
    published_at  TEXT,                    -- ISO-8601
    fetched_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    content_hash  TEXT,                    -- dedup + incremental re-fetch
    popularity    INTEGER,                 -- reactions / score / upvotes
    trust_score   REAL,                    -- Phase 4, NULL until computed
    metadata      TEXT                     -- JSON blob for source-specific fields
);

CREATE TABLE chunk (
    id                 INTEGER PRIMARY KEY,
    document_id        INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    ordinal            INTEGER NOT NULL,    -- position within the document
    heading            TEXT,
    text               TEXT    NOT NULL,
    token_count        INTEGER,
    url_anchor         TEXT,                -- deep link to the exact location
    embedding_model    TEXT,                -- e.g. bge-small-en-v1.5 (Phase 2)
    embedding_dims     INTEGER,
    canonical_chunk_id INTEGER REFERENCES chunk(id),  -- near-dup collapse; NULL = canonical
    content_hash       TEXT,
    UNIQUE (document_id, ordinal)
);

-- ---------------------------------------------------------------------------
-- Claims & evidence (the product)
-- ---------------------------------------------------------------------------

CREATE TABLE claim (
    id             INTEGER PRIMARY KEY,
    text           TEXT    NOT NULL,        -- canonical wording
    normalized_key TEXT    UNIQUE,          -- clusters near-identical claims into one
    confidence     REAL,                    -- 0..1, Phase 4
    disputed       INTEGER NOT NULL DEFAULT 0,  -- boolean: strong support AND contradiction
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- provenance: which chunks a claim was extracted from
CREATE TABLE claim_chunk (
    claim_id INTEGER NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
    PRIMARY KEY (claim_id, chunk_id)
);

-- typed edge: claim <-> source chunk
CREATE TABLE evidence (
    id         INTEGER PRIMARY KEY,
    claim_id   INTEGER NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
    chunk_id   INTEGER NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
    relation   TEXT    NOT NULL CHECK (relation IN ('supports', 'contradicts', 'explains')),
    strength   REAL,                        -- 0..1
    rationale  TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (claim_id, chunk_id, relation)
);

-- ---------------------------------------------------------------------------
-- Knowledge graph (entities & typed relations)
-- ---------------------------------------------------------------------------

CREATE TABLE entity (
    id             INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    type           TEXT NOT NULL,           -- tool | library | concept | algorithm
    description    TEXT
);

CREATE TABLE entity_alias (
    entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    alias     TEXT    NOT NULL,             -- Postgres / PostgreSQL / pg
    PRIMARY KEY (entity_id, alias)
);

CREATE TABLE relation (
    id                INTEGER PRIMARY KEY,
    subject_entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    object_entity_id  INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    type              TEXT    NOT NULL,      -- alternative-to | built-with | used-by | part-of | replaces
    strength          REAL,
    document_id       INTEGER REFERENCES document(id) ON DELETE SET NULL,  -- provenance
    UNIQUE (subject_entity_id, object_entity_id, type)
);

-- link a claim to the entities it is about (for subgraph-by-entity queries)
CREATE TABLE claim_entity (
    claim_id  INTEGER NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    PRIMARY KEY (claim_id, entity_id)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

CREATE INDEX idx_chunk_document     ON chunk (document_id);
CREATE INDEX idx_claim_chunk_chunk  ON claim_chunk (chunk_id);
CREATE INDEX idx_evidence_claim     ON evidence (claim_id);
CREATE INDEX idx_evidence_chunk     ON evidence (chunk_id);
CREATE INDEX idx_relation_subject   ON relation (subject_entity_id);
CREATE INDEX idx_relation_object    ON relation (object_entity_id);
CREATE INDEX idx_claim_entity_entity ON claim_entity (entity_id);
CREATE INDEX idx_document_source    ON document (source_type);
