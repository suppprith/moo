"""Polarity opposition rules (app.evidence.opposition).

Pure text functions, so the contradiction rule is testable without a database,
an index or a model — which is the point: the keyless contradiction path is the
one that was silently wrong.
"""

from app.evidence import opposition
from app.evidence.opposition import antonym_conflict, content_terms, opposes, overlap


def test_negated_restatement_opposes():
    kind, strength = opposes(
        "SQLite handles concurrent writes well.",
        "SQLite does not handle concurrent writes well.",
        sim=0.85,
    )
    assert kind == "negation"
    assert 0 < strength <= 0.9


def test_antonym_pair_opposes():
    kind, _ = opposes(
        "Postgres is faster for complex analytical joins.",
        "Postgres is slower for complex analytical joins.",
        sim=0.85,
    )
    assert kind == "antonym"


def test_deprecation_opposes_recommendation():
    assert opposes(
        "datetime.utcnow() is the recommended way to get the current UTC time.",
        "datetime.utcnow() is deprecated and should be avoided.",
        sim=0.70,
    )


def test_same_subject_is_required():
    """Opposite polarity about two different things is not a disagreement."""
    assert (
        opposes(
            "Postgres is faster for analytical joins.",
            "Redis snapshots are slower to load than the append-only file.",
            sim=0.20,
        )
        is None
    )


def test_different_axes_do_not_oppose():
    assert (
        opposes(
            "Redis AOF is more durable than RDB snapshots.",
            "Redis RDB snapshots are faster to load than AOF.",
            sim=0.72,
        )
        is None
    )


def test_a_text_weighing_both_sides_opposes_nobody():
    both = "Postgres is faster for reads but slower for wide writes."
    assert antonym_conflict("Postgres is faster for reads.", both) is None


def test_incidental_negation_is_not_opposition():
    """The failure mode this module exists to fix: a negation somewhere in
    related prose used to be enough to record a contradiction."""
    assert (
        opposes(
            "SQLite handles concurrent writes well in WAL mode.",
            "Do not delete the -wal file while the database is open.",
            sim=0.55,
        )
        is None
    )


def test_polarity_words_are_not_subject_matter():
    terms = content_terms("SQLite does not handle concurrent writes well")
    assert "not" not in terms and "sqlite" in terms
    assert overlap("faster", "slower") == 0.0


def test_similarity_floor_is_enforced():
    a, b = "WAL mode is enabled by default.", "WAL mode is not enabled by default."
    assert opposes(a, b, sim=opposition.SUBJECT_SIM - 0.01) is None
    assert opposes(a, b, sim=opposition.SUBJECT_SIM + 0.01)


def test_empty_text_never_opposes():
    assert opposes("", "SQLite does not do that.", sim=0.9) is None
    assert opposes("   ", "", sim=0.9) is None
