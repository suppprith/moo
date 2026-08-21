"""Unit tests for the heuristic evidence classifier (app.evidence.links)."""

from app.evidence.links import _assertions, _classify_heuristic

CLAIM = "SQLite handles concurrent writes well in WAL mode."


def test_high_similarity_supports():
    rel, strength = _classify_heuristic(
        "Postgres handles joins well.", "Postgres handles joins well.", 0.72
    )
    assert rel == "supports"
    assert strength == 0.72


def test_negated_restatement_contradicts():
    rel, _ = _classify_heuristic(
        CLAIM, "SQLite does not handle concurrent writes well in WAL mode.", 0.78
    )
    assert rel == "contradicts"


def test_antonym_contradicts():
    rel, _ = _classify_heuristic(
        "Postgres is faster than MySQL for analytical joins.",
        "Postgres is slower than MySQL for analytical joins.",
        0.80,
    )
    assert rel == "contradicts"


def test_contrast_word_alone_is_not_a_contradiction():
    """The old rule fired on any "not"/"however" in a related chunk, which is
    most technical prose. It has to say the opposite, not merely say "not"."""
    rel, _ = _classify_heuristic(
        CLAIM,
        "However, the write-ahead log is not the same file as the database, "
        "so backups have to include it.",
        0.58,
    )
    assert rel != "contradicts"


def test_causal_cue_explains():
    rel, _ = _classify_heuristic(
        "The process died under load.",
        "This happens because the OOM killer reaps the process.",
        0.48,
    )
    assert rel == "explains"


def test_a_close_restatement_supports_even_when_it_gives_a_reason():
    """Explanation only wins in the middle band; a chunk that restates the claim
    outright still supports it, reason or no reason."""
    rel, _ = _classify_heuristic(
        "The process died under load.",
        "The process died under load because the OOM killer reaped it.",
        0.71,
    )
    assert rel == "supports"


def test_unrelated_gets_no_edge():
    assert _classify_heuristic(CLAIM, "Some unrelated prose about the weather.", 0.15) is None


def test_extracted_claims_are_preferred_over_raw_prose():
    """A long chunk almost always contains some negation somewhere; the claims
    extracted from it are single assertions, so they are compared instead."""
    prose = (
        "Journaling is configurable. Nothing here says anything about write "
        "concurrency. Do not delete the -wal file while the database is open."
    )
    rel, _ = _classify_heuristic(
        CLAIM, prose, 0.62, ["SQLite does not handle concurrent writes well in WAL mode."]
    )
    assert rel == "contradicts"


def test_same_document_cannot_contradict_itself():
    """One page qualifying its own statement is one voice, not a dispute."""
    rel, _ = _classify_heuristic(
        CLAIM,
        "SQLite does not handle concurrent writes well in WAL mode.",
        0.78,
        allow_contradiction=False,
    )
    assert rel == "supports"


def test_assertions_prefers_claims_then_sentences():
    assert _assertions("whatever", ["a claim"]) == ["a claim"]
    sentences = _assertions(
        "SQLite handles concurrent writes well in WAL mode. "
        "The write-ahead log lives beside the database file on disk."
    )
    assert len(sentences) == 2
    assert _assertions("short") == ["short"]
