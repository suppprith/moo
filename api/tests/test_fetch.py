"""Unit tests for citation drill-down resolvers."""

import sqlite3

import pytest

from app import fetch, ids


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE document (
            id INTEGER PRIMARY KEY, source_type TEXT, url TEXT, title TEXT,
            author TEXT, author_role TEXT, published_at TEXT, updated_at TEXT,
            fetched_at TEXT, popularity INTEGER, trust_score REAL, raw_text TEXT
        );
        CREATE TABLE chunk (
            id INTEGER PRIMARY KEY, document_id INTEGER, ordinal INTEGER,
            heading TEXT, text TEXT, url_anchor TEXT, canonical_chunk_id INTEGER
        );
        CREATE TABLE claim (id INTEGER PRIMARY KEY, text TEXT, confidence REAL, disputed INTEGER);
        CREATE TABLE evidence (
            id INTEGER PRIMARY KEY, claim_id INTEGER, chunk_id INTEGER,
            relation TEXT, strength REAL, rationale TEXT
        );
        CREATE TABLE claim_chunk (claim_id INTEGER, chunk_id INTEGER);
        CREATE TABLE claim_entity (claim_id INTEGER, entity_id INTEGER);
        """
    )
    c.execute(
        "INSERT INTO document VALUES (5,'docs','https://pg/docs','PG Docs',NULL,'maintainer',"
        "'2024-01-01',NULL,'2024-02-01',3,0.95,'full document body text')"
    )
    c.execute("INSERT INTO chunk VALUES (74,5,0,'Intro','intro text','https://pg/docs#intro',NULL)")
    c.execute("INSERT INTO chunk VALUES (75,5,1,'Joins','joins body text','https://pg/docs#joins',NULL)")
    c.execute("INSERT INTO chunk VALUES (76,5,2,'End','ending text','https://pg/docs#end',NULL)")
    c.execute("INSERT INTO claim VALUES (7,'Postgres handles joins well',0.8,0)")
    c.execute("INSERT INTO evidence VALUES (1,7,75,'supports',0.9,'directly states it')")
    c.execute("INSERT INTO evidence VALUES (2,7,76,'contradicts',0.7,'benchmark disagrees')")
    c.execute("INSERT INTO claim_chunk VALUES (7,75)")
    c.execute("INSERT INTO claim_entity VALUES (7,3)")
    c.commit()
    return c


def test_fetch_document(conn):
    out = fetch.fetch_document(conn, 5)
    assert out["id"] == "doc_5"
    assert out["text"] == "full document body text"
    assert out["trust_score"] == 0.95 and out["author_role"] == "maintainer"
    assert [ch["id"] for ch in out["chunks"]] == ["chk_74", "chk_75", "chk_76"]


def test_fetch_document_missing_returns_none(conn):
    assert fetch.fetch_document(conn, 999) is None


def test_fetch_chunk_context_and_document_handle(conn):
    out = fetch.fetch_chunk(conn, 75)
    assert out["id"] == "chk_75" and out["document"] == "doc_5"
    assert out["url"] == "https://pg/docs#joins"
    assert out["text"] == "joins body text"
    assert out["context"]["prev"]["id"] == "chk_74"
    assert out["context"]["next"]["id"] == "chk_76"
    assert "preview" in out["context"]["prev"]


def test_fetch_chunk_at_document_start_has_no_prev(conn):
    out = fetch.fetch_chunk(conn, 74)
    assert out["context"]["prev"] is None
    assert out["context"]["next"]["id"] == "chk_75"


def test_fetch_claim_full_evidence_set(conn):
    out = fetch.fetch_claim(conn, 7)
    assert out["id"] == "clm_7" and out["confidence"] == 0.8
    assert out["evidence"][0]["relation"] == "contradicts"
    assert out["evidence"][0]["source"] == "chk_76"
    assert out["evidence"][1]["source"] == "chk_75"
    assert out["evidence"][1]["excerpt"] == "joins body text"
    assert out["extracted_from"] == ["chk_75"]
    assert out["entities"] == ["ent_3"]


def test_fetch_handle_dispatch(conn):
    assert fetch.fetch_handle(conn, "doc_5")["id"] == "doc_5"
    assert fetch.fetch_handle(conn, "chk_75")["id"] == "chk_75"
    assert fetch.fetch_handle(conn, "clm_7")["id"] == "clm_7"


def test_fetch_handle_unknown_row_returns_none(conn):
    assert fetch.fetch_handle(conn, "chk_9999") is None


def test_fetch_handle_rejects_unresolvable_kind(conn):
    with pytest.raises(ValueError):
        fetch.fetch_handle(conn, "ent_3")
    with pytest.raises(ValueError):
        fetch.fetch_handle(conn, "not-a-handle")
