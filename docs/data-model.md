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

## Search contract — agent shaping (SUP-105, v1.1)

`/search` (and `app.search.search`) returns a versioned contract
(`meta.contract_version`). Two axes shape the payload for the consumer,
independent of `mode`:

- **`format`** — `full` (default, the UI shape: every top-level key present) or
  `agent` (compact: opaque handles instead of bare rowids, evidence referenced
  by source handle instead of repeating the URL, null/empty sections dropped).
- **`fields`** — comma-separated subset of `sources,claims,graph,answer,citations`
  to include; `meta` and the query echo are always kept. Omitted → the mode's
  default sections. (`full` format with no `fields` is the untouched v1.0 shape.)
- **`offset` / `cursor`** — paginate the `sources` list. `meta.page` carries
  `{offset, limit, returned, has_more, next_cursor}`; `next_cursor` is an opaque
  base64 handle passed back as `cursor` to fetch the next page.

**Opaque handles** ([`app/ids.py`](../api/app/ids.py)) address rows across the
agent surface: `chk_<id>` (chunk / a search "source"), `clm_<id>` (claim),
`doc_<id>` (document), `ent_<id>` (entity). Handles are stable (rowid-backed) and
routable — the fetch/drill-down endpoints (SUP-106) decode the prefix. Callers
treat them as opaque.

## Serving contract — errors & streaming (SUP-107)

**Structured errors.** Every API error renders as one shape so an agent can
branch on `code` and retry only when `retryable` is true:

```json
{"error": {"code": "not_found", "message": "...", "retryable": false, "request_id": "…"}}
```

Codes ([`app/errors.py`](../api/app/errors.py)): `invalid_request` (422),
`not_found` (404), `unauthorized` (401), `rate_limited` (429, retryable),
`budget_exceeded` (429), `timeout` (504, retryable), `upstream_error` (502,
retryable), `internal` (500, retryable). Every request (success or error) carries
an `X-Request-ID` header, echoed in the envelope's `request_id`.

**Fetch / drill-down.** `GET /source/{id}` (document), `GET /chunk/{id}`,
`GET /claim/{id}` resolve a handle (or bare rowid) from an agent-format response
to the full row ([`app/fetch.py`](../api/app/fetch.py)); a chunk carries its
document handle + prev/next context, a claim its full evidence set
(contradictions first) + provenance.

**Streaming.** `GET /search/stream` returns `text/event-stream`
([`app/streaming.py`](../api/app/streaming.py)): a `progress` event, then one
`source`/`claim` event per row, then a terminal `done` — or a terminal `error`
event if it fails mid-stream (the 200 status is already committed). The
deep-research endpoint (SUP-114) reuses this event schema.

## Deep research (SUP-110–114)

The deep-research engine (`app/research/`) wraps the pipeline in
planner -> iterative multi-hop loop -> persisted run -> cited report:

- **`POST /research`** `{question, k, max_steps, max_seconds, use_llm}` -> the full
  cited report `{executive_answer, findings[], disputed_points[], open_questions[],
  sources[], run_id, status, steps[], coverage[], budget}`. Budget enforced
  end-to-end; a run that hits a cap returns `status: partial`.
- **`POST /research/stream`** streams the same run over SSE (`app/streaming.py`
  events): `plan` -> a `progress` event per step (with `reason`:
  plan/gap/contradiction) -> terminal `report` + `done`, or `error`.
- **`GET /research/{run_id}`** re-fetches a persisted run's status + report for
  polling/resume (report reassembled deterministically, no LLM).

Runs persist to `research_run` / `research_step` / `research_claim` (migration
0005); they associate with the shared claim/evidence graph rather than owning it,
so a run is purgeable without harming the graph.

**Cost & caching (SUP-122).** Every LLM stage (expand, plan, claims, links,
rerank, synthesize) is cached in `llm_cache` by content hash via
`llm.cached_json`, so a repeated identical run makes ~zero API calls (plan/expand
cache their heuristic results too, so repeats hit the cache even keyless).
`run_research` resets `llm` cost counters and reports a `cost` block:
`{plan_ms, loop_ms, llm_calls, cache_hits, cache_misses}`. The loop is bounded by
`max_steps` + a wall-clock deadline, never re-runs the same query, and suppresses
near-duplicate spawned follow-ups (token-Jaccard) so it does not re-retrieve
covered ground. Measured warm on the dev box: a 3-step run ≈ 0.8s loop + ~1ms
plan; retrieval-only path is well under 1s (post the near-dup simhash fix).

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
