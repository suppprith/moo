"""Unit tests for graph traversal (app.graph.store)."""

import sqlite3

from app.graph import store


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE document (id INTEGER PRIMARY KEY, url TEXT, title TEXT);
        CREATE TABLE entity (id INTEGER PRIMARY KEY, canonical_name TEXT UNIQUE,
                             type TEXT, description TEXT);
        CREATE TABLE entity_alias (entity_id INTEGER, alias TEXT,
                                   PRIMARY KEY (entity_id, alias));
        CREATE TABLE relation (id INTEGER PRIMARY KEY, subject_entity_id INTEGER,
                               object_entity_id INTEGER, type TEXT, strength REAL,
                               document_id INTEGER);
        """
    )
    conn.executemany(
        "INSERT INTO entity (id, canonical_name, type) VALUES (?, ?, ?)",
        [(1, "A", "tool"), (2, "B", "tool"), (3, "C", "concept"), (4, "D", "tool")],
    )
    conn.execute("INSERT INTO entity_alias VALUES (1, 'a-alias')")
    conn.execute("INSERT INTO document VALUES (5, 'http://x', 'Doc')")
    conn.executemany(
        "INSERT INTO relation (id, subject_entity_id, object_entity_id, type, strength, document_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(1, 1, 2, "alternative-to", 0.6, 5), (2, 3, 2, "part-of", 0.9, None)],
    )
    return conn


def test_resolve_canonical_and_alias():
    conn = _db()
    assert store.resolve(conn, "A") == 1
    assert store.resolve(conn, "a-alias") == 1
    assert store.resolve(conn, "nope") is None


def test_neighbors_undirected_both_directions():
    conn = _db()
    assert {n["neighbor"]["id"] for n in store.neighbors(conn, 2)} == {1, 3}
    assert {n["neighbor"]["id"] for n in store.neighbors(conn, 1)} == {2}


def test_neighbors_type_filter():
    conn = _db()
    got = store.neighbors(conn, 2, rel_types=["part-of"])
    assert [n["neighbor"]["id"] for n in got] == [3]


def test_subgraph_depth_and_provenance():
    conn = _db()
    one = store.subgraph(conn, 1, depth=1)
    assert {n["id"] for n in one["nodes"]} == {1, 2}
    two = store.subgraph(conn, 1, depth=2)
    assert {n["id"] for n in two["nodes"]} == {1, 2, 3}
    edge = next(e for e in two["edges"] if e["id"] == 1)
    assert edge["provenance"]["url"] == "http://x"


def test_subgraph_isolated_entity_returns_lone_node():
    conn = _db()
    g = store.subgraph(conn, 4, depth=2)
    assert [n["id"] for n in g["nodes"]] == [4]
    assert g["edges"] == []


def test_shortest_path_walks_and_prunes_cycles():
    conn = _db()
    p = store.shortest_path(conn, 1, 3)
    assert [n["id"] for n in p["nodes"]] == [1, 2, 3]
    assert len(p["edges"]) == 2
    assert store.shortest_path(conn, 1, 4) is None
    assert store.shortest_path(conn, 1, 1)["edges"] == []
