"""Cited report assembler."""

import sqlite3

import pytest

from app.research import report as report_mod


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE document (id INTEGER PRIMARY KEY, url TEXT, title TEXT, source_type TEXT, trust_score REAL);
        CREATE TABLE chunk (id INTEGER PRIMARY KEY, document_id INTEGER, canonical_chunk_id INTEGER);
        CREATE TABLE claim (id INTEGER PRIMARY KEY, text TEXT, confidence REAL, disputed INTEGER);
        CREATE TABLE claim_chunk (claim_id INTEGER, chunk_id INTEGER);
        CREATE TABLE evidence (id INTEGER PRIMARY KEY, claim_id INTEGER, chunk_id INTEGER, relation TEXT, strength REAL);
        """
    )
    c.execute("INSERT INTO document VALUES (1,'https://pg/docs','PG Docs','docs',0.95)")
    c.execute("INSERT INTO document VALUES (2,'https://blog/x','A Blog','blog',0.4)")
    c.execute("INSERT INTO chunk VALUES (10,1,NULL)")
    c.execute("INSERT INTO chunk VALUES (11,1,NULL)")
    c.execute("INSERT INTO chunk VALUES (20,2,NULL)")
    c.execute("INSERT INTO claim VALUES (1,'Postgres handles joins well',0.8,0)")
    c.execute("INSERT INTO evidence VALUES (1,1,10,'supports',0.9)")
    c.execute("INSERT INTO claim VALUES (2,'X is always faster',0.5,1)")
    c.execute("INSERT INTO evidence VALUES (2,2,10,'supports',0.7)")
    c.execute("INSERT INTO evidence VALUES (3,2,20,'contradicts',0.7)")
    c.execute("INSERT INTO claim VALUES (3,'Unsupported assertion',0.6,0)")
    c.execute("INSERT INTO claim VALUES (4,'Self-contradicting in one doc',0.5,1)")
    c.execute("INSERT INTO evidence VALUES (4,4,10,'supports',0.7)")
    c.execute("INSERT INTO evidence VALUES (5,4,11,'contradicts',0.7)")
    c.commit()
    return c


def _run():
    return {
        "question": "Is Postgres good for joins?",
        "claims": [
            {"id": 1, "text": "Postgres handles joins well", "confidence": 0.8, "disputed": False, "sub_question_id": 1, "chunk_ids": [10]},
            {"id": 2, "text": "X is always faster", "confidence": 0.5, "disputed": True, "sub_question_id": 2, "chunk_ids": [10, 20]},
            {"id": 3, "text": "Unsupported assertion", "confidence": 0.6, "disputed": False, "sub_question_id": 3, "chunk_ids": []},
            {"id": 4, "text": "Self-contradicting in one doc", "confidence": 0.5, "disputed": True, "sub_question_id": 1, "chunk_ids": [10, 11]},
        ],
        "coverage": [
            {"sub_question_id": 1, "question": "how are joins?", "covered": True},
            {"sub_question_id": 3, "question": "what about the uncovered bit?", "covered": False},
        ],
    }


def test_report_shape(conn):
    r = report_mod.assemble_report(conn, _run(), use_llm=False)
    assert set(r) >= {"executive_answer", "findings", "disputed_points", "open_questions", "sources"}


def test_uncited_claim_excluded_from_findings(conn):
    r = report_mod.assemble_report(conn, _run(), use_llm=False)
    texts = {f["text"] for f in r["findings"]}
    assert "Unsupported assertion" not in texts
    assert "Postgres handles joins well" in texts
    assert all(f["citations"] for f in r["findings"])


def test_disputed_point_is_two_sided(conn):
    r = report_mod.assemble_report(conn, _run(), use_llm=False)
    assert len(r["disputed_points"]) == 1
    dp = r["disputed_points"][0]
    assert dp["text"] == "X is always faster"
    assert dp["supports"] and dp["contradicts"]
    assert set(dp["supports"]).isdisjoint(dp["contradicts"])


def test_same_document_dispute_is_not_two_sided(conn):
    r = report_mod.assemble_report(conn, _run(), use_llm=False)
    dp_texts = {dp["text"] for dp in r["disputed_points"]}
    assert "Self-contradicting in one doc" not in dp_texts
    finding = next(f for f in r["findings"] if f["text"] == "Self-contradicting in one doc")
    assert finding["disputed"] is False


def test_open_questions_from_uncovered_subquestions(conn):
    r = report_mod.assemble_report(conn, _run(), use_llm=False)
    assert r["open_questions"] == ["what about the uncovered bit?"]


def test_sources_have_trust_tier_and_handle(conn):
    r = report_mod.assemble_report(conn, _run(), use_llm=False)
    by_doc = {s["document_id"]: s for s in r["sources"]}
    assert by_doc[1]["trust_tier"] == "high" and by_doc[1]["handle"] == "doc_1"
    assert by_doc[2]["trust_tier"] == "low"
    assert len(r["sources"]) == len({s["document_id"] for s in r["sources"]})


def test_executive_answer_is_grounded_and_cited(conn):
    r = report_mod.assemble_report(conn, _run(), use_llm=False)
    assert "[S" in r["executive_answer"]
    assert r["generator"] == "template"


def test_empty_run_yields_empty_report(conn):
    r = report_mod.assemble_report(conn, {"question": "q", "claims": [], "coverage": []}, use_llm=False)
    assert r["findings"] == [] and r["executive_answer"] == "" and r["sources"] == []
