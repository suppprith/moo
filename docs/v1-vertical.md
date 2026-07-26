# v1 vertical — Databases for application developers

The whole system is built and evaluated against **one narrow slice**. Depth over breadth:
better to nail a small domain than be shallow everywhere. Everything downstream (connectors,
retrieval, claims, evidence, eval) targets this scope.

## Domain

**Operational databases that a backend developer chooses between and operates**, focused on
four engines: **PostgreSQL, MySQL/MariaDB, SQLite, and Redis**.

The center of gravity is the *decisions and gotchas* a developer hits: "which one for X",
"why is this slow", "what does this setting actually do", "why did this break in production".
This is where sources genuinely disagree — the sweet spot for an evidence graph.

### In scope

- **Engine selection & comparison** — Postgres vs MySQL, Redis vs Postgres for caching/queues,
  SQLite vs Postgres for small/embedded apps.
- **Query performance & indexing** — B-tree/GIN/GiST/hash indexes, `EXPLAIN`/`EXPLAIN ANALYZE`,
  slow queries, N+1, when an index is *not* used.
- **Transactions, isolation & locking** — isolation levels, MVCC, deadlocks, `SELECT ... FOR UPDATE`,
  lost updates, Redis transactions/`MULTI`.
- **Data types & modeling** — `JSONB` vs `JSON`, `TEXT` vs `VARCHAR`, `NUMERIC` vs float,
  UUID vs bigint PKs, normalization tradeoffs.
- **Operations & reliability** — connection pooling (PgBouncer), `VACUUM`/autovacuum & bloat,
  replication basics, backups, WAL, Redis persistence (RDB/AOF) & eviction.
- **Common failure modes** — connection exhaustion, table/index bloat, lock contention,
  OOM/eviction, `max_connections`, integer overflow of an `int4` PK.

### Out of scope (v1)

- NoSQL document/wide-column stores beyond Redis (MongoDB, Cassandra, DynamoDB).
- Analytical/OLAP & warehouses (ClickHouse, Snowflake, BigQuery, DuckDB).
- Vector databases as a topic (we *use* sqlite-vec internally; it is not domain content).
- Cloud-managed specifics (RDS/Aurora/Cloud SQL knobs), ORMs as their own topic, and
  distributed NewSQL (CockroachDB, Vitess, Spanner) — revisit post-v1.

## Seed sources (16)

Enumerated across the source types the connectors ingest. Trust tiers are the trust rubric
(official docs > maintainer > benchmark/academic > eng blog > accepted SO > forum).

### Official docs (highest trust)

1. **PostgreSQL manual** — https://www.postgresql.org/docs/current/ (esp. MVCC, indexes, `EXPLAIN`, VACUUM)
2. **MySQL 8 reference manual** — https://dev.mysql.com/doc/refman/8.0/en/
3. **SQLite docs** — https://www.sqlite.org/docs.html (esp. "Appropriate Uses", WAL, datatypes)
4. **Redis docs** — https://redis.io/docs/latest/ (data types, persistence, eviction, transactions)

### GitHub repos — issues / PRs / discussions / release notes (the "dark knowledge")

5. **postgres/postgres** (mirror) + **pgbouncer/pgbouncer** — pooling issues
6. **redis/redis** — issues & changelogs (persistence, eviction, latency)
7. **sqlite/sqlite** (GitHub mirror) + **mariadb/server**
8. **prisma/prisma**, **sqlalchemy/sqlalchemy** — issues where DB behavior bites the ORM layer

### Engineering blogs (mid trust, high signal)

9. **Use The Index, Luke!** — https://use-the-index-luke.com/ (indexing bible)
10. **PostgreSQL wiki** — Don't Do This / performance pages — https://wiki.postgresql.org/
11. **Percona blog** — https://www.percona.com/blog/ (MySQL/Postgres ops & benchmarks)
12. **Brandur Leach** — https://brandur.org/ (Postgres in production)
13. **PlanetScale / Vitess blog** — https://planetscale.com/blog (MySQL scaling)
14. **Redis blog** — https://redis.io/blog/

### Community (contrarian / experience-based evidence)

15. **Stack Overflow** tags: `postgresql`, `mysql`, `sqlite`, `redis`, `database-indexing`,
    `database-performance`, `transactions`, `deadlock`
16. **Hacker News** (Algolia API) + **Reddit** r/PostgreSQL, r/Database, r/redis

## Gold queries (15)

Drive the eval (P/R/nDCG + claim accuracy). Mix of **why / comparison / troubleshooting /
how-to / definition**. "Expected" = the substance a correct evidence-backed answer must contain;
several are deliberately **disputed** to exercise the contradiction path.

| #  | Query | Intent | Expected answer notes |
| -- | ----- | ------ | --------------------- |
| 1  | Why is my Postgres query not using the index I created? | troubleshoot | Planner cost choice: low selectivity / small table → seq scan cheaper; type mismatch or function on column; stale stats (run `ANALYZE`); `LIKE '%x'` leading wildcard; missing composite-column order. |
| 2  | Postgres vs MySQL for a new web app in 2024 | comparison | **Disputed.** Postgres: richer types (JSONB, arrays), stricter SQL, extensions; MySQL: simpler replication, familiar, fast simple reads. Sources disagree — surface both, don't average. |
| 3  | Should I use JSONB or a normalized schema in Postgres? | comparison | JSONB for sparse/variable/rarely-queried attrs; normalize for relational integrity & heavy querying. Note GIN index cost, no FKs inside JSONB. Disputed by degree. |
| 4  | Why do my database connections keep getting exhausted? | troubleshoot | `max_connections` + one connection per request without pooling; each PG connection = a process (RAM); fix with PgBouncer/pooler; serverless multiplies it; check for leaked/un-closed connections. |
| 5  | What is the difference between VARCHAR and TEXT in Postgres? | definition | No performance difference in Postgres (unlike MySQL); `varchar(n)` only adds a length check. Contrast with MySQL where it matters. |
| 6  | How do I fix a deadlock in MySQL/Postgres? | how-to | Consistent lock ordering; keep transactions short; lower isolation if safe; retry on deadlock error (40P01 / 1213); read the deadlock log/`SHOW ENGINE INNODB STATUS`. |
| 7  | Is SQLite good enough for production? | why/comparison | **Disputed.** Yes for single-writer / read-heavy / embedded / low-to-mid concurrency (WAL helps); no for high write concurrency or multi-node. "Appropriate Uses" doc vs skeptics. |
| 8  | Redis vs Postgres for a job/task queue | comparison | Redis: fast, lists/streams, but durability & exactly-once are DIY; Postgres `SELECT FOR UPDATE SKIP LOCKED`: transactional, durable, simpler ops. Tradeoff, disputed. |
| 9  | Why is autovacuum causing performance problems / not keeping up? | troubleshoot | Dead-tuple bloat, long-running txns hold the xmin horizon, cost-delay throttling too conservative; tune `autovacuum_vacuum_scale_factor`, per-table settings; watch for wraparound. |
| 10 | What isolation level should I use and what are the anomalies? | definition/why | Read Committed (PG default) vs Repeatable Read vs Serializable; anomalies: dirty/non-repeatable/phantom reads, write skew; PG RR = snapshot (no phantoms) but not full serializable. |
| 11 | How does MVCC work in Postgres? | definition | Each write creates a new row version; readers don't block writers; visibility via xmin/xmax + snapshot; dead tuples cleaned by VACUUM; explains bloat & long-txn issues. |
| 12 | When should I add an index and when does it hurt? | why | Speeds reads/lookups, costs write throughput + storage; useless on low-cardinality/small tables; over-indexing slows writes & VACUUM; composite column order matters. |
| 13 | Why did my BIGINT/INT primary key run out / overflow? | troubleshoot | `int4` PK caps at ~2.1B; migrate to `bigint`; sequences don't reuse gaps; classic prod outage; how to migrate a live PK type. |
| 14 | RDB vs AOF: how should I configure Redis persistence? | how-to/comparison | RDB = periodic snapshot (fast restart, can lose recent writes); AOF = append log (more durable, `appendfsync everysec`), larger/slower; many run both. Data-loss tradeoff. |
| 15 | How do I make a slow `SELECT COUNT(*)` faster in Postgres? | how-to | Exact count is O(n) due to MVCC (no cached count like MyISAM); use `EXPLAIN` estimate / `pg_class.reltuples`, or a maintained counter/materialized view; `count(*)` vs `count(col)`. |

### Notes for the eval harness

- Queries **2, 3, 7, 8** are the primary **disputed** cases — the evidence layer must show
  contradiction, not collapse to one side.
- Relevance judgments (which chunks are relevant per query) are authored against the
  ingested corpus and versioned in-repo.

## Multi-domain expansion

v1 stays the deep, end-to-end proof (**databases**). Further CS/coding verticals
start shallower and deepen over time — the gate to moo being a coding agent's
*default* web_search. The seed registry lives in
[`api/app/ingest/seeds.py`](../api/app/ingest/seeds.py) as a `Vertical` per
domain (github repos, docs sites, SO tags, subreddits, HN queries, keywords).

| Vertical | Seeds (examples) | Sample gold queries |
| --- | --- | --- |
| databases (deep) | postgres/redis/sqlite repos, SQLite/PG docs, SO db tags | the 15 above |
| languages | cpython, node; Python docs; `python`/`node.js` SO | "how does the Python GIL affect threads?"; "Node event loop vs worker threads" |
| web-frameworks | fastapi, django; FastAPI docs | "when should a FastAPI route be async vs sync?"; "Django ORM N+1 query" |
| build-tooling | uv, pip; uv docs | "uv vs pip for dependency resolution"; "why is my wheel build failing?" |
| cloud-infra | kubernetes, terraform; k8s docs | "why is my pod stuck in CrashLoopBackOff?"; "terraform state drift" |
| systems | (docs/community only) | "what causes the OOM killer to fire?"; "how do syscalls work?" |

**Routing.** `is_domain_relevant` checks the union of every vertical's keywords,
so ingestion + retrieval span domains while off-topic (non-CS) community noise is
still filtered out. Ingest one vertical at a time:
`uv run python -m app.ingest <connector> --vertical <name>`.

**Scale / vector store.** sqlite-vec does an exact linear scan; the point where a
dedicated ANN store (Qdrant/FAISS) earns its ops cost is ~500k–1M vectors (see
[data-model.md](data-model.md)). The corpus is ~200 chunks per shallow vertical
today; a full-depth multi-domain corpus would approach that threshold and trigger
the migration. Until then sqlite-vec's exactness + zero-ops win.
