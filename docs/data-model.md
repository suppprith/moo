# Data model (SUP-71)

Single SQLite file (`api/data/moo.sqlite`). Stdlib `sqlite3`, no ORM. Schema lives in
[`api/migrations/0001_init.sql`](../api/migrations/0001_init.sql); apply with
`uv run python -m app.db`.

## Entities

| Table          | What it holds                                                            |
| -------------- | ------------------------------------------------------------------------ |
| `document`     | One ingested source (issue, PR, docs page, blog, SO/HN/Reddit post). Trust inputs: `author_role`, `popularity`, `published_at`. |
| `chunk`        | Heading-aware slice of a document (~300–500 tokens). Carries deep-link anchor + embedding metadata; `canonical_chunk_id` collapses near-dups. |
| `claim`        | A canonical declarative statement. `normalized_key` clusters near-identical claims; `confidence` + `disputed` set in Phase 4. |
| `evidence`     | **The typed edge** `claim → chunk`, `relation ∈ {supports, contradicts, explains}` with a `strength`. Contradictions are first-class, never averaged away. |
| `claim_chunk`  | Provenance: which chunks a claim was *extracted* from (distinct from evidence). |
| `entity`       | Domain thing (tool / library / concept / algorithm); `entity_alias` holds Postgres/PostgreSQL/pg. |
| `relation`     | Typed edge `entity → entity` (`alternative-to`, `built-with`, `used-by`, `part-of`, `replaces`) with provenance `document_id`. |
| `claim_entity` | Which entities a claim is about — powers subgraph-by-entity queries.      |

## ER sketch

```
 document 1───∞ chunk ∞───∞ claim ∞───∞ entity
    │            │   └────────┐  │  ┌──────┘ │
    │            │  (claim_chunk)│(claim_entity)
    │            │            │  │           │
    │            └──∞ evidence ∞─┘           │
    │              (supports/contradicts/    │
    │               explains, strength)      │
    └──────── relation (entity↔entity) ──────┘
             (provenance → document)

 legend:  1───∞ one-to-many      ∞───∞ many-to-many (join table)
 evidence:   claim ─┬─ supports  ──▶ chunk
                    ├─ contradicts──▶ chunk
                    └─ explains   ──▶ chunk
```

## Planned in Phase 2 (not created yet)

Indexing tables are added by a later migration so Phase 0 stays dependency-free:

- **FTS5** — `chunk_fts` virtual table (`external content` on `chunk`) over `text` + `heading`
  + document `title`, field-weighted for BM25 (SUP-79).
- **sqlite-vec** — `chunk_vec` `vec0` virtual table holding chunk embeddings for top-k
  similarity (SUP-78). Requires loading the `sqlite-vec` extension at connect time.

Both are derived from `chunk`, so they are rebuildable from the tables above.

## Conventions

- Timestamps are ISO-8601 text (`datetime('now')`).
- `PRAGMA foreign_keys = ON` and `journal_mode = WAL` on every connection (`app.db.get_connection`).
- Migrations are immutable, numbered `NNNN_name.sql`, tracked in `schema_migrations`.
