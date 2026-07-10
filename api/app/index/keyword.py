"""Keyword index: SQLite FTS5 with BM25 (SUP-79).

A standalone FTS5 table ``chunk_fts`` indexes each chunk's document title,
heading, and body as separate columns so BM25 can weight title/heading above
body. Keyword search catches exact identifiers (error strings, flag names,
`SKIP LOCKED`, `int4`) that embeddings blur. Kept in sync with `chunk` by a
rebuild job (the chunking step already rebuilds `chunk` in bulk).

Build:   ``uv run python -m app.index.keyword build``
Search:  ``uv run python -m app.index.keyword search "SELECT FOR UPDATE SKIP LOCKED"``
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3

from ..db import get_connection, migrate

log = logging.getLogger("moo.index.keyword")

# BM25 column weights: title/heading rank above body.
W_TITLE, W_HEADING, W_BODY = 10.0, 8.0, 1.0


def build(conn: sqlite3.Connection) -> int:
    """(Re)build the FTS5 index from `chunk` + `document`. Returns rows indexed."""
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5("
        "title, heading, body, chunk_id UNINDEXED, tokenize = 'porter unicode61')"
    )
    conn.execute("DELETE FROM chunk_fts")
    conn.execute(
        "INSERT INTO chunk_fts(rowid, title, heading, body, chunk_id) "
        "SELECT ch.id, coalesce(d.title, ''), coalesce(ch.heading, ''), ch.text, ch.id "
        "FROM chunk ch JOIN document d ON d.id = ch.document_id"
    )
    conn.commit()
    return conn.execute("SELECT count(*) FROM chunk_fts").fetchone()[0]


def _match_query(text: str, *, operator: str = "AND") -> str:
    """Turn free text into a safe FTS5 MATCH: each term a quoted phrase.

    AND (implicit) is right for identifier-ish queries; OR is the fallback for
    long natural-language queries where no chunk contains every word (BM25
    still ranks fuller matches first).
    """
    terms = re.findall(r"\w+", text)
    joiner = " " if operator == "AND" else " OR "
    return joiner.join(f'"{t}"' for t in terms)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    k: int = 10,
    source_types: list[str] | None = None,
    since: str | None = None,
) -> list[tuple[int, float]]:
    """Return [(chunk_id, bm25_relevance)] best-first (higher = better).

    Runs strict AND first (exact-identifier precision); if that returns fewer
    than k hits, tops up with OR-mode matches so long natural-language queries
    still contribute a BM25 ranking to the hybrid fusion.
    """
    hits = _search_match(conn, _match_query(query), k, source_types, since)
    if len(hits) < k:
        seen = {cid for cid, _ in hits}
        hits += [
            (cid, score)
            for cid, score in _search_match(
                conn, _match_query(query, operator="OR"), k, source_types, since
            )
            if cid not in seen
        ]
    return hits[:k]


def _search_match(
    conn: sqlite3.Connection,
    match: str,
    k: int,
    source_types: list[str] | None,
    since: str | None,
) -> list[tuple[int, float]]:
    if not match:
        return []
    sql = (
        f"SELECT f.chunk_id, bm25(chunk_fts, {W_TITLE}, {W_HEADING}, {W_BODY}) AS score "
        "FROM chunk_fts f "
        "JOIN chunk ch ON ch.id = f.chunk_id "
        "JOIN document d ON d.id = ch.document_id "
        "WHERE chunk_fts MATCH ?"
    )
    params: list[object] = [match]
    if source_types:
        sql += f" AND d.source_type IN ({','.join('?' * len(source_types))})"
        params += source_types
    if since:
        sql += " AND d.published_at >= ?"
        params.append(since)
    sql += " ORDER BY score LIMIT ?"
    params.append(k)
    # bm25() is negative (more negative = better); flip to positive relevance
    return [(cid, -score) for cid, score in conn.execute(sql, params)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.index.keyword")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("-k", type=int, default=10)
    sp.add_argument("--source", action="append", dest="sources")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    migrate()
    conn = get_connection()
    if args.cmd == "build":
        n = build(conn)
        print(f"keyword index: {n} chunks indexed")
    else:
        hits = search(conn, args.query, k=args.k, source_types=args.sources)
        for cid, score in hits:
            row = conn.execute(
                "SELECT d.source_type, substr(ch.text, 1, 90) t "
                "FROM chunk ch JOIN document d ON d.id = ch.document_id WHERE ch.id = ?",
                (cid,),
            ).fetchone()
            print(f"  {score:6.2f}  {row['source_type']:14} {row['t'].strip()!r}")
        if not hits:
            print("  (no matches)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
