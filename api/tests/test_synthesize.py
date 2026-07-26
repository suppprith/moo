"""Unit tests for answer synthesis (app.synthesize)."""

import sqlite3

from app import synthesize


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE document (id INTEGER PRIMARY KEY, url TEXT, title TEXT,
                               source_type TEXT, trust_score REAL);
        CREATE TABLE chunk (id INTEGER PRIMARY KEY, document_id INTEGER,
                            canonical_chunk_id INTEGER);
        CREATE TABLE claim (id INTEGER PRIMARY KEY, text TEXT, confidence REAL, disputed INTEGER);
        CREATE TABLE claim_chunk (claim_id INTEGER, chunk_id INTEGER);
        CREATE TABLE evidence (id INTEGER PRIMARY KEY, claim_id INTEGER, chunk_id INTEGER,
                               relation TEXT, strength REAL);
        """
    )
    for did in (10, 20):
        conn.execute(
            "INSERT INTO document VALUES (?, ?, ?, 'docs', 0.9)",
            (did, f"http://doc/{did}", f"Doc {did}"),
        )
    conn.execute("INSERT INTO chunk VALUES (1, 10, NULL)")
    conn.execute("INSERT INTO chunk VALUES (2, 20, NULL)")
    return conn


def _claim(conn, cid, text, conf, disputed):
    conn.execute("INSERT INTO claim VALUES (?, ?, ?, ?)", (cid, text, conf, disputed))


def test_undisputed_claim_gets_cited_sentence():
    conn = _db()
    _claim(conn, 1, "Postgres has JSONB", 0.8, 0)
    conn.execute("INSERT INTO evidence (claim_id, chunk_id, relation, strength) VALUES (1, 1, 'supports', 0.9)")
    out = synthesize.synthesize(conn, "q", [{"id": 1, "text": "Postgres has JSONB", "confidence": 0.8, "disputed": False}], use_llm=False)
    assert "[S1]" in out["answer"]
    assert out["sources"][0]["url"] == "http://doc/10"
    assert out["disputed"] == []


def test_disputed_claim_renders_both_sides():
    conn = _db()
    _claim(conn, 1, "SQLite is production ready", 0.5, 1)
    conn.execute("INSERT INTO evidence (claim_id, chunk_id, relation, strength) VALUES (1, 1, 'supports', 0.8)")
    conn.execute("INSERT INTO evidence (claim_id, chunk_id, relation, strength) VALUES (1, 2, 'contradicts', 0.8)")
    claims = [{"id": 1, "text": "SQLite is production ready", "confidence": 0.5, "disputed": True}]
    out = synthesize.synthesize(conn, "q", claims, use_llm=False)
    assert "disagree" in out["answer"].lower()
    assert "[S1]" in out["answer"] and "[S2]" in out["answer"]
    assert out["disputed"] == ["SQLite is production ready"]


def test_provenance_used_when_no_evidence_edges():
    conn = _db()
    _claim(conn, 1, "claim from provenance", 0.6, 0)
    conn.execute("INSERT INTO claim_chunk VALUES (1, 1)")
    out = synthesize.synthesize(conn, "q", [{"id": 1, "text": "claim from provenance", "confidence": 0.6, "disputed": False}], use_llm=False)
    assert "[S1]" in out["answer"]


def test_unciteable_claim_excluded():
    conn = _db()
    _claim(conn, 1, "no sources at all", 0.6, 0)
    out = synthesize.synthesize(conn, "q", [{"id": 1, "text": "no sources at all", "confidence": 0.6, "disputed": False}], use_llm=False)
    assert out["answer"] == "" and out["generator"] == "none"


def test_validation_drops_uncited_sentences():
    valid = {1, 2}
    text = "This sentence has a citation [S1]. This one is uncited and must go. Another good one [S2]."
    kept = synthesize._validate(text, valid)
    assert "[S1]" in kept and "[S2]" in kept
    assert "must go" not in kept


def test_llm_answer_falls_back_to_template_when_all_uncited(monkeypatch):
    conn = _db()
    _claim(conn, 1, "Postgres has JSONB", 0.8, 0)
    conn.execute("INSERT INTO evidence (claim_id, chunk_id, relation, strength) VALUES (1, 1, 'supports', 0.9)")
    monkeypatch.setattr(synthesize, "_llm_answer", lambda *a, **k: "A confident but uncited answer.")
    claims = [{"id": 1, "text": "Postgres has JSONB", "confidence": 0.8, "disputed": False}]
    out = synthesize.synthesize(conn, "q", claims, use_llm=True)
    assert out["generator"] == "template" and "[S1]" in out["answer"]
