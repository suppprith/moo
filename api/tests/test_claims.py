"""Unit tests for claim-extraction helpers (app.evidence.claims), no network."""

from app.evidence.claims import _candidate_score, _heuristic_extract, _normalize, _sentences


def test_sentences_flattens_and_filters():
    text = (
        "# Heading\n\nPostgres handles complex joins better than MySQL in most cases.\n\n"
        "```sql\nSELECT 1;\n```\n\nIs this good?\n\nShort.\n"
    )
    sents = _sentences(text)
    assert any("complex joins better" in s for s in sents)
    assert all("\n" not in s for s in sents)
    assert all(not s.endswith("?") for s in sents)
    assert all("SELECT 1" not in s for s in sents)


def test_sentences_length_bounds():
    assert _sentences("Too short here.") == []
    long = " ".join(["word"] * 60) + "."
    assert _sentences(long) == []


def test_candidate_score_rewards_assertions_and_entities():
    strong = _candidate_score("Postgres is faster than MySQL for complex joins here.")
    weak = _candidate_score("The meeting is scheduled for next tuesday afternoon today.")
    assert strong > weak
    assert strong >= 3


def test_heuristic_extract_links_to_chunk():
    chunks = [
        (1, "Redis is faster than Postgres for simple key lookups in most benchmarks."),
        (2, "The weather today is quite nice and sunny outside right now."),
    ]
    claims = _heuristic_extract(chunks, max_claims=5)
    assert claims
    assert claims[0]["chunk_id"] == 1


def test_normalize():
    assert _normalize("  Postgres, MySQL!! ") == "postgres mysql"
