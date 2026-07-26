"""Vector index with sqlite-vec.

A ``vec0`` virtual table ``chunk_vec`` holds the chunk embeddings and answers
top-k similarity queries. Metadata filtering (source type / date) is done by
over-fetching from the KNN and intersecting with an id set selected from
``chunk``/``document`` — simple and exact, and it sidesteps NULL handling in
vec0 metadata columns. A numpy brute-force search is kept for recall/latency
comparison (see ``docs/data-model.md`` for the sqlite-vec vs Qdrant note).

Build:   ``uv run python -m app.index.vector build``
Search:  ``uv run python -m app.index.vector search "why is my query slow"``
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time

import numpy as np
import sqlite_vec

from ..db import DEFAULT_DB_PATH, get_connection, migrate
from ..embed import DEFAULT_MODEL, embed_texts

log = logging.getLogger("moo.index.vector")

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def load_vec(conn: sqlite3.Connection) -> None:
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def connect(db_path=DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = get_connection(db_path)
    load_vec(conn)
    return conn


def _dims(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT embedding_dims FROM chunk WHERE embedding_dims IS NOT NULL LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else 384


def build(conn: sqlite3.Connection, *, rebuild: bool = False) -> int:
    """Create chunk_vec and sync embeddings into it. Returns rows inserted.

    Sync must self-heal, not just append: a rechunk deletes every chunk row and
    SQLite reuses the freed rowids, so chunk_vec can hold *old* vectors under
    ids that now belong to different chunks. We prune orphans, drop rows whose
    stored vector no longer matches chunk.embedding, then insert what's missing.
    """
    dims = _dims(conn)
    if rebuild:
        conn.execute("DROP TABLE IF EXISTS chunk_vec")
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0("
        f"chunk_id INTEGER PRIMARY KEY, embedding float[{dims}])"
    )
    conn.execute("DELETE FROM chunk_vec WHERE chunk_id NOT IN (SELECT id FROM chunk)")
    stale = [
        r[0]
        for r in conn.execute(
            "SELECT cv.chunk_id FROM chunk_vec cv JOIN chunk c ON c.id = cv.chunk_id "
            "WHERE c.embedding IS NULL OR cv.embedding != c.embedding"
        )
    ]
    conn.executemany("DELETE FROM chunk_vec WHERE chunk_id = ?", [(i,) for i in stale])
    rows = conn.execute(
        "SELECT id, embedding FROM chunk "
        "WHERE embedding IS NOT NULL AND id NOT IN (SELECT chunk_id FROM chunk_vec)"
    ).fetchall()
    conn.executemany(
        "INSERT INTO chunk_vec(chunk_id, embedding) VALUES (?, ?)",
        [(r["id"], r["embedding"]) for r in rows],
    )
    conn.commit()
    if stale:
        log.info("repaired %d stale vector rows", len(stale))
    return len(rows)


def _allowed_ids(
    conn: sqlite3.Connection, source_types: list[str] | None, since: str | None
) -> set[int] | None:
    if not source_types and not since:
        return None
    sql = "SELECT ch.id FROM chunk ch JOIN document d ON d.id = ch.document_id WHERE 1=1"
    params: list[object] = []
    if source_types:
        sql += f" AND d.source_type IN ({','.join('?' * len(source_types))})"
        params += source_types
    if since:
        sql += " AND d.published_at >= ?"
        params.append(since)
    return {r[0] for r in conn.execute(sql, params)}


def search(
    conn: sqlite3.Connection,
    query_vec: bytes,
    *,
    k: int = 10,
    source_types: list[str] | None = None,
    since: str | None = None,
) -> list[tuple[int, float]]:
    """Return [(chunk_id, cosine_similarity)] best-first."""
    allowed = _allowed_ids(conn, source_types, since)
    fetch = k if allowed is None else k * 8
    try:
        rows = conn.execute(
            "SELECT chunk_id, distance FROM chunk_vec "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (query_vec, fetch),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "chunk_vec" in str(exc):
            return []
        raise
    out: list[tuple[int, float]] = []
    for chunk_id, dist in rows:
        if allowed is not None and chunk_id not in allowed:
            continue
        out.append((chunk_id, 1.0 - (dist * dist) / 2.0))  # normalized: cos = 1 - L2^2/2
        if len(out) >= k:
            break
    return out


def embed_query(text: str, model_name: str = DEFAULT_MODEL) -> bytes:
    vec = embed_texts([QUERY_PREFIX + text], model_name)[0]
    return vec.tobytes()


def search_text(conn: sqlite3.Connection, query: str, **kw) -> list[tuple[int, float]]:
    return search(conn, embed_query(query), **kw)


def brute_search(
    conn: sqlite3.Connection, query_vec: bytes, k: int = 10
) -> list[tuple[int, float]]:
    """Exact top-k by cosine over all stored vectors (baseline for eval)."""
    rows = conn.execute(
        "SELECT id, embedding FROM chunk WHERE embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        return []
    ids = np.array([r["id"] for r in rows])
    mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    q = np.frombuffer(query_vec, dtype=np.float32)
    sims = mat @ q
    top = np.argsort(-sims)[:k]
    return [(int(ids[i]), float(sims[i])) for i in top]


def bench(conn: sqlite3.Connection, queries: list[str], k: int = 10) -> dict[str, float]:
    """Compare sqlite-vec KNN to exact brute force: recall@k + latency."""
    vecs = [embed_query(q) for q in queries]
    recall_sum = ann_ms = brute_ms = 0.0
    for v in vecs:
        t0 = time.perf_counter()
        ann = {c for c, _ in search(conn, v, k=k)}
        ann_ms += (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        brute = {c for c, _ in brute_search(conn, v, k=k)}
        brute_ms += (time.perf_counter() - t0) * 1000
        recall_sum += len(ann & brute) / k
    n = len(queries)
    return {
        "recall@k": recall_sum / n,
        "ann_ms": ann_ms / n,
        "brute_ms": brute_ms / n,
    }


_BENCH_QUERIES = [
    "why is my postgres query not using the index",
    "should I use JSONB or a normalized schema",
    "database connections keep getting exhausted",
    "is SQLite good enough for production",
    "how does MVCC work in postgres",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.index.vector")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").add_argument("--rebuild", action="store_true")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("-k", type=int, default=10)
    sp.add_argument("--source", action="append", dest="sources")
    sub.add_parser("bench").add_argument("-k", type=int, default=10)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    migrate()
    conn = connect()
    if args.cmd == "build":
        n = build(conn, rebuild=getattr(args, "rebuild", False))
        total = conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0]
        print(f"vector index: +{n} rows synced, {total} total")
    elif args.cmd == "bench":
        m = bench(conn, _BENCH_QUERIES, k=args.k)
        print(
            f"recall@{args.k}={m['recall@k']:.3f}  "
            f"sqlite-vec={m['ann_ms']:.2f} ms  brute-force={m['brute_ms']:.2f} ms  "
            f"(n={len(_BENCH_QUERIES)} queries)"
        )
    else:
        t0 = time.perf_counter()
        hits = search_text(conn, args.query, k=args.k, source_types=args.sources)
        ms = (time.perf_counter() - t0) * 1000
        for cid, sim in hits:
            row = conn.execute(
                "SELECT d.source_type, ch.url_anchor, substr(ch.text,1,90) t "
                "FROM chunk ch JOIN document d ON d.id = ch.document_id WHERE ch.id = ?",
                (cid,),
            ).fetchone()
            print(f"  {sim:.3f}  {row['source_type']:14} {row['t'].strip()!r}")
        print(f"[{ms:.0f} ms incl. query embedding]")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
