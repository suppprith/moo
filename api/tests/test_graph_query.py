"""Unit tests for subgraph-for-query assembly + API (app.graph.query, app.main)."""

import sqlite3

import pytest

from app.graph import query as gq


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
        CREATE TABLE claim (id INTEGER PRIMARY KEY, text TEXT, confidence REAL,
                            disputed INTEGER DEFAULT 0);
        CREATE TABLE claim_entity (claim_id INTEGER, entity_id INTEGER,
                                   PRIMARY KEY (claim_id, entity_id));
        """
    )
    conn.executemany(
        "INSERT INTO entity (id, canonical_name, type) VALUES (?, ?, ?)",
        [(1, "PostgreSQL", "tool"), (2, "MySQL", "tool"), (3, "JSONB", "concept")],
    )
    conn.executemany(
        "INSERT INTO entity_alias VALUES (?, ?)",
        [(1, "postgres"), (1, "pg"), (2, "mysql")],
    )
    conn.executemany(
        "INSERT INTO relation (id, subject_entity_id, object_entity_id, type, strength, document_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(1, 1, 2, "alternative-to", 0.6, None), (2, 3, 1, "part-of", 0.9, None)],
    )
    conn.executemany(
        "INSERT INTO claim (id, text, confidence) VALUES (?, ?, ?)",
        [
            (1, "PostgreSQL beats MySQL for JSON workloads.", 0.8),  # both endpoints
            (2, "JSONB is a binary JSON type in Postgres.", 0.9),     # one endpoint
        ],
    )
    return conn


def test_link_claims_matches_aliases_and_is_idempotent():
    conn = _db()
    first = gq.link_claims(conn)
    assert first > 0
    # claim 1 mentions Postgres + MySQL; claim 2 mentions JSONB + Postgres
    got = {
        (r["claim_id"], r["entity_id"])
        for r in conn.execute("SELECT claim_id, entity_id FROM claim_entity")
    }
    assert (1, 1) in got and (1, 2) in got and (2, 3) in got and (2, 1) in got
    assert gq.link_claims(conn) == 0  # nothing new the second time


def test_graph_for_query_seeds_and_attaches_edge_claims():
    conn = _db()
    gq.link_claims(conn)
    g = gq.graph_for_query(conn, "postgres vs mysql")
    assert set(g["seeds"]) == {1, 2}
    edge = next(e for e in g["edges"] if {e["source"], e["target"]} == {1, 2})
    # claim 1 mentions BOTH endpoints -> it backs the edge
    assert {c["id"] for c in edge["evidence"]["claims"]} == {1}
    # node claims present, top-level claim list deduped
    node1 = next(n for n in g["nodes"] if n["id"] == 1)
    assert any(c["id"] == 1 for c in node1["claims"])


def test_graph_for_query_no_entities_returns_note():
    conn = _db()
    g = gq.graph_for_query(conn, "something unrelated entirely")
    assert g["seeds"] == [] and g["nodes"] == [] and "note" in g


def test_expand_node_by_name_and_id():
    conn = _db()
    gq.link_claims(conn)
    by_name = gq.expand_node(conn, "pg")
    by_id = gq.expand_node(conn, "1")
    assert by_name["center"]["id"] == by_id["center"]["id"] == 1
    assert {n["id"] for n in by_name["nodes"]} == {1, 2, 3}
    assert gq.expand_node(conn, "unknown-thing") is None


def test_api_graph_endpoints(monkeypatch):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from app import main

    def fresh_conn():  # the endpoint closes its conn, so hand out a new one each call
        conn = _db()
        gq.link_claims(conn)
        return conn

    monkeypatch.setattr(main, "get_connection", fresh_conn)

    client = fastapi_testclient.TestClient(main.app)
    r = client.get("/graph", params={"q": "postgres vs mysql"})
    assert r.status_code == 200 and set(r.json()["seeds"]) == {1, 2}
    r = client.get("/graph/expand", params={"node": "Redis-unknown"})
    assert r.status_code == 404
