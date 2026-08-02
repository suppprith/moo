"""Retrieval eval: grading, metrics, and the ablation runner."""

import pytest

from app.db import get_connection, migrate
from app.eval.retrieval import (
    chunk_meta,
    dcg,
    format_deltas,
    format_table,
    grade_for,
    judged_pool,
    load_judgments,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    run_eval,
    score_ranking,
)

JUDGMENTS = [
    {"url_contains": "sqlite.org/wal.html", "grade": 3},
    {"url_contains": "wiki.postgresql.org", "heading_contains": "timestamp", "grade": 3},
    {"url_contains": "wiki.postgresql.org", "grade": 1},
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "eval.sqlite"
    migrate(path)
    conn = get_connection(path)
    docs = [
        (1, "https://sqlite.org/wal.html", "docs"),
        (2, "https://wiki.postgresql.org/wiki/Don%27t_Do_This", "docs"),
        (3, "https://example.dev/unrelated", "blog"),
    ]
    for doc_id, url, source_type in docs:
        conn.execute(
            "INSERT INTO document (id, url, source_type, title) VALUES (?, ?, ?, ?)",
            (doc_id, url, source_type, url),
        )
    chunks = [
        (10, 1, "", "wal text"),
        (11, 2, "Don't use timestamp (without time zone)", "timestamp text"),
        (12, 2, "Don't use char(n)", "char text"),
        (13, 3, "", "unrelated"),
    ]
    for ordinal, (chunk_id, doc_id, heading, text) in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunk (id, document_id, ordinal, heading, text, url_anchor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chunk_id, doc_id, ordinal, heading, text, ""),
        )
    conn.commit()
    yield conn
    conn.close()


def test_grade_uses_the_best_matching_judgment():
    assert grade_for(JUDGMENTS, "https://sqlite.org/wal.html", None) == 3
    assert grade_for(JUDGMENTS, "https://wiki.postgresql.org/x", "Don't use char(n)") == 1
    assert grade_for(JUDGMENTS, "https://elsewhere.dev", "timestamp") == 0


def test_a_heading_judgment_only_covers_its_section():
    """One long document is the answer to one query and noise for another."""
    url = "https://wiki.postgresql.org/wiki/Don%27t_Do_This"
    assert grade_for(JUDGMENTS, url, "Don't use timestamp to store UTC times") == 3
    assert grade_for(JUDGMENTS, url, "Authentication") == 1


def test_grading_is_case_insensitive():
    assert grade_for(JUDGMENTS, "HTTPS://SQLite.org/WAL.html", None) == 3


def test_precision_counts_against_k_not_the_result_length():
    assert precision_at_k([3, 0, 2, 0, 0], 5) == 0.4
    assert precision_at_k([3, 3], 5) == 0.4, "a short result list is not free precision"
    assert precision_at_k([], 5) == 0.0


def test_recall_is_bounded_and_needs_a_denominator():
    assert recall_at_k([3, 1, 0], 3, total_relevant=4) == 0.5
    assert recall_at_k([3, 3, 3], 3, total_relevant=2) == 1.0
    assert recall_at_k([3], 1, total_relevant=0) is None


def test_dcg_rewards_putting_the_best_first():
    assert dcg([3, 1]) > dcg([1, 3])


def test_ndcg_is_one_for_a_perfect_ranking():
    assert ndcg_at_k([3, 2, 1], [3, 2, 1], 3) == 1.0
    assert ndcg_at_k([1, 2, 3], [3, 2, 1], 3) < 1.0
    assert ndcg_at_k([0, 0], [], 2) is None, "nothing judged means no score, not zero"


def test_ndcg_ideal_comes_from_the_corpus_pool():
    """Retrieval cannot be blamed for documents the store does not have."""
    assert ndcg_at_k([3], [3], 10) == 1.0


def test_mrr_finds_the_first_relevant_rank():
    assert mrr([0, 0, 2]) == round(1 / 3, 4)
    assert mrr([3]) == 1.0
    assert mrr([0, 0]) == 0.0


def test_judged_pool_counts_only_what_the_store_has(db):
    pool = judged_pool(db, JUDGMENTS)
    assert sorted(pool) == [1, 3, 3], "wal and the timestamp section at 3, the char section at 1"


def test_overlapping_judgments_do_not_double_count_a_chunk(db):
    """The timestamp chunk matches both the heading judgment (3) and the
    document-wide one (1). Counting it twice would inflate the denominator."""
    assert len(judged_pool(db, JUDGMENTS)) == 3
    assert sorted(judged_pool(db, [JUDGMENTS[1], JUDGMENTS[2]])) == [1, 3]


def test_chunk_meta_reads_url_and_heading(db):
    meta = chunk_meta(db, [10, 11])
    assert meta[10][0] == "https://sqlite.org/wal.html"
    assert meta[11][1].startswith("Don't use timestamp")


def test_score_ranking_scores_one_query(db):
    query = {"id": "q", "judgments": JUDGMENTS}
    perfect = score_ranking(db, [10, 11, 12], query, k=10)
    assert perfect["ndcg@10"] == 1.0
    assert perfect["mrr"] == 1.0
    assert perfect["hits"] == 3
    assert perfect["judged_in_corpus"] == 3

    poor = score_ranking(db, [13, 12], query, k=10)
    assert poor["mrr"] == 0.5
    assert poor["ndcg@10"] < perfect["ndcg@10"]


def test_an_empty_ranking_scores_zero_rather_than_crashing(db):
    row = score_ranking(db, [], {"id": "q", "judgments": JUDGMENTS}, k=10)
    assert row["p@5"] == 0.0 and row["mrr"] == 0.0 and row["hits"] == 0


def test_run_eval_compares_configurations(db):
    queries = [{"id": "q", "query": "wal", "judgments": JUDGMENTS}]
    result = run_eval(db, queries, {
        "good": lambda q: [10, 11],
        "bad": lambda q: [13],
        "broken": lambda q: (_ for _ in ()).throw(RuntimeError("index missing")),
    }, k=10)
    good = result["configs"]["good"]["summary"]
    assert good["ndcg@10"] > result["configs"]["bad"]["summary"]["ndcg@10"]
    assert result["configs"]["broken"]["summary"]["mrr"] == 0.0, "a failure scores, not skips"
    assert result["n_queries"] == 1


def test_the_table_and_deltas_read_as_an_ablation(db):
    queries = [{"id": "q", "query": "wal", "judgments": JUDGMENTS}]
    result = run_eval(db, queries, {"a": lambda q: [13], "b": lambda q: [10, 11]}, k=10)
    assert "retrieval eval" in format_table(result)
    deltas = format_deltas(result)
    assert "better" in deltas and "vs a" in deltas


def test_claim_accuracy_scores_faithfulness_and_source_quality(db, monkeypatch):
    """A claim whose words are nowhere in the chunk it cites is the failure this
    catches; one lifted from a judged-relevant source is the good case."""
    db.execute("INSERT INTO claim (id, text, confidence, disputed) VALUES (1, ?, 0.8, 0)",
               ("wal mode allows concurrent readers and writers",))
    db.execute("INSERT INTO claim (id, text, confidence, disputed) VALUES (2, ?, 0.4, 1)",
               ("something entirely unrelated to any source text",))
    db.execute("UPDATE chunk SET text = ? WHERE id = 10",
               ("wal mode allows concurrent readers and writers in sqlite",))
    db.execute("INSERT INTO claim_chunk (claim_id, chunk_id) VALUES (1, 10)")
    db.execute("INSERT INTO claim_chunk (claim_id, chunk_id) VALUES (2, 13)")
    db.commit()

    import app.search as search_mod

    monkeypatch.setattr(search_mod, "search", lambda *a, **k: {
        "claims": [
            {"id": 1, "text": "wal mode allows concurrent readers and writers",
             "disputed": False},
            {"id": 2, "text": "something entirely unrelated to any source text",
             "disputed": True},
        ]
    })

    from app.eval.retrieval import claim_accuracy

    result = claim_accuracy(db, [{"id": "q", "query": "wal", "judgments": JUDGMENTS}])
    assert result["claims"] == 2
    assert result["pct_well_grounded"] == 0.5
    assert result["pct_from_judged_relevant"] == 0.5
    assert result["pct_disputed"] == 0.5
    assert result["orphans"] == 0


def test_a_claim_with_no_provenance_is_counted_as_an_orphan(db, monkeypatch):
    db.execute("INSERT INTO claim (id, text) VALUES (3, 'a claim citing nothing')")
    db.commit()
    import app.search as search_mod

    monkeypatch.setattr(search_mod, "search", lambda *a, **k: {
        "claims": [{"id": 3, "text": "a claim citing nothing", "disputed": False}]
    })
    from app.eval.retrieval import claim_accuracy

    result = claim_accuracy(db, [{"id": "q", "query": "wal", "judgments": JUDGMENTS}])
    assert result["orphans"] == 1
    assert result["mean_overlap"] == 0.0


class TestJudgmentFile:
    """The judgments are the artifact; a malformed one silently flatters."""

    def test_versioned_and_documented(self):
        data = load_judgments()
        assert data["version"] and data["authored_on"] and data["corpus_note"]

    def test_queries_are_complete_and_unique(self):
        data = load_judgments()
        ids = [q["id"] for q in data["queries"]]
        assert len(ids) == len(set(ids))
        assert len(ids) >= 10
        for query in data["queries"]:
            assert query["query"] and query["intent"]
            assert query["judgments"], f"{query['id']} judges nothing"
            for judgment in query["judgments"]:
                assert judgment["url_contains"]
                assert 1 <= judgment["grade"] <= 3

    def test_every_query_has_a_top_grade(self):
        """A query with no grade-3 document has no right answer to find."""
        for query in load_judgments()["queries"]:
            assert any(j["grade"] == 3 for j in query["judgments"]), query["id"]
