"""Unit tests for confidence scoring (app.evidence.confidence)."""

import sqlite3

from app.evidence.confidence import DISPUTE_MIN, score_claim


def _db() -> sqlite3.Connection:
    """Minimal in-memory schema for the evidence-scoring query."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE document (id INTEGER PRIMARY KEY, trust_score REAL);
        CREATE TABLE chunk (id INTEGER PRIMARY KEY, document_id INTEGER,
                            canonical_chunk_id INTEGER);
        CREATE TABLE claim (id INTEGER PRIMARY KEY);
        CREATE TABLE evidence (id INTEGER PRIMARY KEY, claim_id INTEGER, chunk_id INTEGER,
                               relation TEXT, strength REAL);
        """
    )
    conn.execute("INSERT INTO claim (id) VALUES (1)")
    return conn


def _add(conn, chunk_id, doc_id, trust, relation, strength, canonical=None):
    conn.execute("INSERT OR IGNORE INTO document VALUES (?, ?)", (doc_id, trust))
    conn.execute("INSERT INTO chunk VALUES (?, ?, ?)", (chunk_id, doc_id, canonical))
    conn.execute(
        "INSERT INTO evidence (claim_id, chunk_id, relation, strength) VALUES (1, ?, ?, ?)",
        (chunk_id, relation, strength),
    )


def test_all_support_high_confidence():
    conn = _db()
    _add(conn, 10, 100, 0.9, "supports", 0.9)
    _add(conn, 11, 101, 0.8, "supports", 0.8)
    r = score_claim(conn, 1)
    assert r["confidence"] > 0.6 and not r["disputed"]


def test_strong_both_sides_is_disputed():
    conn = _db()
    _add(conn, 10, 100, 0.9, "supports", 0.9)
    _add(conn, 11, 101, 0.9, "contradicts", 0.9)
    r = score_claim(conn, 1)
    assert r["disputed"]
    assert r["support_mass"] >= DISPUTE_MIN and r["contradiction_mass"] >= DISPUTE_MIN


def test_independence_same_document_counts_once():
    conn = _db()
    # three chunks from the SAME document must not triple-count
    _add(conn, 10, 100, 0.8, "supports", 0.9)
    _add(conn, 11, 100, 0.8, "supports", 0.9)
    _add(conn, 12, 100, 0.8, "supports", 0.9)
    one = score_claim(conn, 1)
    # a second, genuinely independent document should raise the mass
    _add(conn, 20, 200, 0.8, "supports", 0.9)
    two = score_claim(conn, 1)
    assert two["support_mass"] > one["support_mass"]
    assert one["breakdown"]["supporting_sources"] == 1


def test_near_dup_repost_counts_once():
    conn = _db()
    # chunk 11 is a near-dup of chunk 10 (canonical points at 10's document)
    _add(conn, 10, 100, 0.8, "supports", 0.9)
    _add(conn, 11, 200, 0.8, "supports", 0.9, canonical=10)
    r = score_claim(conn, 1)
    assert r["breakdown"]["supporting_sources"] == 1  # repost collapsed


def test_no_evidence_zero_confidence():
    conn = _db()
    assert score_claim(conn, 1)["confidence"] == 0.0
