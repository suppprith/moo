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

## Decision note — vector store: sqlite-vec vs Qdrant (SUP-78)

**Choice: sqlite-vec, revisit at ~500k–1M vectors.** Keeping vectors in the same SQLite file
means one file to ship, back up, and self-host — no separate service, which matters for the
Linux/self-hosting audience. sqlite-vec does a linear scan per query; at v1 corpus scale that
is sub-10ms and returns exact results.

Measured on the current corpus (187 chunks, 384-dim, `python -m app.index.vector bench`):

| method      | recall@10 | latency |
| ----------- | --------- | ------- |
| sqlite-vec  | 1.000     | ~3.7 ms |
| brute force | 1.000 (=) | ~2.8 ms |

At this size brute force is even a hair faster — the point where a dedicated ANN store
(Qdrant/FAISS/HNSW) earns its operational cost is roughly **500k–1M vectors** or when p99 query
latency crosses ~50–100 ms. Until then sqlite-vec's exactness and zero-ops win. The embeddings
also live as a BLOB on `chunk`, so switching stores is a re-index, not a re-embed.
