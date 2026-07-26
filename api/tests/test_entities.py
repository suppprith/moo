"""Unit tests for entity/relation extraction (app.graph.entities)."""

import sqlite3

from app.graph.entities import (
    add_relation,
    upsert_entity,
    _alias_index,
    _heuristic_relations,
    seed_domain,
)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entity (id INTEGER PRIMARY KEY, canonical_name TEXT UNIQUE,
                             type TEXT, description TEXT);
        CREATE TABLE entity_alias (entity_id INTEGER, alias TEXT,
                                   PRIMARY KEY (entity_id, alias));
        CREATE TABLE relation (id INTEGER PRIMARY KEY, subject_entity_id INTEGER,
                               object_entity_id INTEGER, type TEXT, strength REAL,
                               document_id INTEGER,
                               UNIQUE (subject_entity_id, object_entity_id, type));
        """
    )
    return conn


def test_dedup_by_alias_keeps_one_node():
    conn = _db()
    idx = {}
    a = upsert_entity(conn, idx, "PostgreSQL", "tool", aliases=["postgres", "pg"])
    b = upsert_entity(conn, idx, "pg", "tool")
    assert a == b
    assert conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 1


def test_description_backfilled_not_overwritten():
    conn = _db()
    idx = {}
    eid = upsert_entity(conn, idx, "Redis", "tool", description="in-memory store")
    upsert_entity(conn, idx, "Redis", "tool", description="something else")
    desc = conn.execute("SELECT description FROM entity WHERE id = ?", (eid,)).fetchone()[0]
    assert desc == "in-memory store"


def test_relation_self_loop_and_bad_type_rejected():
    conn = _db()
    idx = {}
    a = upsert_entity(conn, idx, "MySQL", "tool")
    b = upsert_entity(conn, idx, "MariaDB", "tool")
    assert add_relation(conn, a, a, "replaces") is False
    assert add_relation(conn, a, b, "nonsense") is False
    assert add_relation(conn, b, a, "replaces", strength=0.7) is True


def test_relation_conflict_keeps_strongest_and_provenance():
    conn = _db()
    idx = {}
    a = upsert_entity(conn, idx, "MySQL", "tool")
    b = upsert_entity(conn, idx, "PostgreSQL", "tool")
    add_relation(conn, a, b, "alternative-to", strength=0.4)
    add_relation(conn, a, b, "alternative-to", strength=0.8, document_id=5)
    row = conn.execute(
        "SELECT strength, document_id FROM relation WHERE subject_entity_id=? AND type='alternative-to'",
        (a,),
    ).fetchone()
    assert row["strength"] == 0.8 and row["document_id"] == 5
    assert conn.execute("SELECT COUNT(*) FROM relation").fetchone()[0] == 1


def test_seed_covers_domain_with_aliases():
    conn = _db()
    seed_domain(conn, _alias_index(conn))
    idx = _alias_index(conn)
    for name in ("PostgreSQL", "postgres", "pg", "SQLite", "Redis"):
        assert name.lower() in idx
    assert conn.execute(
        "SELECT COUNT(*) FROM relation WHERE type='part-of'"
    ).fetchone()[0] >= 1


def test_heuristic_alternative_to_needs_comparison_cue():
    conn = _db()
    idx = _alias_index(conn)
    seed_domain(conn, idx)
    before = conn.execute("SELECT COUNT(*) FROM relation WHERE type='alternative-to'").fetchone()[0]
    assert _heuristic_relations(conn, idx, 1, "PostgreSQL and MySQL are databases.") == 0
    n = _heuristic_relations(conn, idx, 7, "PostgreSQL vs MySQL for a new app.")
    assert n == 1
    after = conn.execute(
        "SELECT document_id FROM relation WHERE type='alternative-to'"
    ).fetchone()
    assert after["document_id"] == 7
    assert before == 0
