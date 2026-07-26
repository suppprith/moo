"""Groundedness / faithfulness guard."""

import sqlite3

import pytest

from app.research import groundedness as g
from app.research import report as report_mod


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE chunk (id INTEGER PRIMARY KEY, document_id INTEGER, canonical_chunk_id INTEGER);
        CREATE TABLE evidence (id INTEGER PRIMARY KEY, claim_id INTEGER, chunk_id INTEGER, relation TEXT, strength REAL);
        CREATE TABLE claim_chunk (claim_id INTEGER, chunk_id INTEGER);
        """
    )
    c.execute("INSERT INTO chunk VALUES (10, 1, NULL)")
    c.execute("INSERT INTO chunk VALUES (20, 2, NULL)")
    c.execute("INSERT INTO evidence VALUES (1, 1, 10, 'supports', 0.9)")
    c.execute("INSERT INTO claim_chunk VALUES (1, 20)")
    c.execute("INSERT INTO evidence VALUES (2, 2, 10, 'supports', 0.8)")
    c.commit()
    return c


def _report(findings, answer="Answer [S1]"):
    return {
        "executive_answer": answer,
        "findings": findings,
        "sources": [{"index": 1, "document_id": 1}, {"index": 2, "document_id": 2}],
    }


def test_backing_documents(conn):
    assert g._backing_documents(conn, 1) == {1, 2}
    assert g._backing_documents(conn, 2) == {1}
    assert g._backing_documents(conn, 999) == set()


def test_grounded_finding_passes(conn):
    r = g.attach_groundedness(conn, _report([{"claim": "clm_1", "citations": [1]}]))
    f = r["findings"][0]
    assert f["grounded"] is True and f["independent_support"] == 2
    assert r["groundedness"]["pct_grounded"] == 1.0
    assert r["groundedness"]["ungrounded"] == []
    assert r["groundedness"]["well_supported"] == 1


def test_hallucinated_citation_flagged_ungrounded(conn):
    r = g.attach_groundedness(conn, _report([{"claim": "clm_2", "citations": [2]}]))
    f = r["findings"][0]
    assert f["grounded"] is False
    assert r["groundedness"]["ungrounded"] == ["clm_2"]
    assert r["groundedness"]["pct_grounded"] == 0.0


def test_finding_with_no_citations_is_ungrounded(conn):
    r = g.attach_groundedness(conn, _report([{"claim": "clm_1", "citations": []}]))
    assert r["findings"][0]["grounded"] is False


def test_answer_citations_validity(conn):
    ok = g.attach_groundedness(conn, _report([{"claim": "clm_1", "citations": [1]}], answer="A [S1] B [S2]"))
    assert ok["groundedness"]["answer_citations_valid"] is True
    bad = g.attach_groundedness(conn, _report([{"claim": "clm_1", "citations": [1]}], answer="A [S9]"))
    assert bad["groundedness"]["answer_citations_valid"] is False


def test_empty_report_is_trivially_grounded(conn):
    r = g.attach_groundedness(conn, {"executive_answer": "", "findings": [], "sources": []})
    assert r["groundedness"]["pct_grounded"] == 1.0 and r["groundedness"]["findings_total"] == 0


def test_assembled_report_has_zero_ungrounded(monkeypatch):
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
    c.execute("INSERT INTO document VALUES (1,'u','T','docs',0.9)")
    c.execute("INSERT INTO chunk VALUES (10,1,NULL)")
    c.execute("INSERT INTO claim VALUES (1,'PG handles joins well',0.8,0)")
    c.execute("INSERT INTO evidence VALUES (1,1,10,'supports',0.9)")
    c.commit()
    run = {"question": "q", "coverage": [],
           "claims": [{"id": 1, "text": "PG handles joins well", "confidence": 0.8, "disputed": False, "chunk_ids": [10]}]}
    rep = report_mod.assemble_report(c, run, use_llm=False)
    assert rep["groundedness"]["findings_total"] == 1
    assert rep["groundedness"]["ungrounded"] == []
    assert all(f["grounded"] for f in rep["findings"])
