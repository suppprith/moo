"""Two sources that genuinely disagree must end up as a dispute.

The keyless contradiction path had unit coverage for the classifier and still
produced nothing useful, so this test runs the real thing: real embeddings,
real retrieval, real linker, real confidence scoring — over a two-document
store where one page says the opposite of the other. It is the regression test
for `contradiction_recall = 0.0`.
"""

import numpy as np
import pytest

from app.db import migrate
from app.embed import embed_texts
from app.evidence.confidence import score_claim
from app.evidence.links import link_claim
from app.index import keyword, vector

CLAIM_A = "Autovacuum is enabled by default and reclaims dead tuples automatically."
CLAIM_B = "Autovacuum is disabled by default and never reclaims dead tuples on its own."

DOC_A = (
    "Autovacuum is enabled by default and reclaims dead tuples automatically. "
    "The daemon wakes periodically and vacuums tables whose dead-tuple count has "
    "crossed the configured threshold, so routine maintenance needs no cron job."
)
DOC_B = (
    "Autovacuum is disabled by default and never reclaims dead tuples on its own. "
    "Operators are expected to schedule VACUUM themselves, because the daemon will "
    "not wake for tables below the configured threshold."
)


@pytest.fixture
def store(tmp_path):
    """Two documents from different hosts, each with its claim, fully indexed."""
    path = tmp_path / "moo.sqlite"
    migrate(path)
    conn = vector.connect(path)

    vectors = embed_texts([DOC_A, DOC_B])
    claim_ids = {}
    for n, (url, text, claim_text) in enumerate(
        [
            ("https://www.postgresql.org/docs/16/routine-vacuuming.html", DOC_A, CLAIM_A),
            ("https://example.dev/blog/autovacuum-myths", DOC_B, CLAIM_B),
        ],
        start=1,
    ):
        conn.execute(
            "INSERT INTO document (id, source_type, url, title, trust_score) VALUES (?,?,?,?,?)",
            (n, "docs", url, f"doc {n}", 0.9),
        )
        conn.execute(
            "INSERT INTO chunk (id, document_id, ordinal, text, embedding_model, embedding_dims) "
            "VALUES (?,?,?,?,?,?)",
            (n, n, 0, text, "test", len(vectors[n - 1])),
        )
        conn.execute(
            "UPDATE chunk SET embedding = ? WHERE id = ?",
            (np.asarray(vectors[n - 1], dtype=np.float32).tobytes(), n),
        )
        cur = conn.execute("INSERT INTO claim (text) VALUES (?)", (claim_text,))
        claim_ids[n] = cur.lastrowid
        conn.execute(
            "INSERT INTO claim_chunk (claim_id, chunk_id) VALUES (?, ?)", (claim_ids[n], n)
        )
    conn.commit()
    keyword.build(conn)
    vector.build(conn, rebuild=True)
    yield conn, claim_ids
    conn.close()


def test_opposing_sources_produce_a_contradiction_edge(store):
    conn, claim_ids = store
    counts = link_claim(conn, claim_ids[1], CLAIM_A, use_llm=False)
    assert counts.get("contradicts"), f"expected a contradiction edge, got {counts}"

    edge = conn.execute(
        "SELECT chunk_id FROM evidence WHERE claim_id = ? AND relation = 'contradicts'",
        (claim_ids[1],),
    ).fetchone()
    assert edge["chunk_id"] == 2, "the contradiction must point at the other document"


def test_a_disagreement_becomes_a_disputed_claim(store):
    conn, claim_ids = store
    link_claim(conn, claim_ids[1], CLAIM_A, use_llm=False)
    scored = score_claim(conn, claim_ids[1])
    assert scored["contradiction_mass"] > 0
    assert scored["confidence"] < 1.0
