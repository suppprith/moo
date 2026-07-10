"""Unit tests for query expansion (app.expand) — heuristic path, no network."""

from app.expand import MAX_EXPANSIONS, _core, heuristic_expand, normalize_query


def test_normalize_strips_punctuation_and_case():
    assert normalize_query("  Why is my QUERY slow?! ") == "why is my query slow"


def test_core_strips_question_scaffolding():
    assert _core("Why is my postgres query slow?") == "postgres query slow"
    assert _core("How do I fix a deadlock?") == "fix a deadlock"
    assert _core("What is MVCC?") == "mvcc"


def test_core_never_empty():
    assert _core("why is my") != ""


def test_heuristic_troubleshooting_adds_error_phrasing():
    out = heuristic_expand("Why do my database connections keep getting exhausted?")
    assert any("error" in q or "fix" in q for q in out)


def test_heuristic_comparison_uses_entities():
    out = heuristic_expand("Redis vs Postgres for a job queue")
    assert any("Redis vs PostgreSQL" in q for q in out)


def test_expansions_deduped_and_capped():
    out = heuristic_expand("postgres slow")
    assert len(out) <= MAX_EXPANSIONS
    normalized = [normalize_query(q) for q in out]
    assert len(set(normalized)) == len(normalized)          # no dups among variants
    assert normalize_query("postgres slow") not in normalized  # original not repeated
