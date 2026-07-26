"""Unit tests for the heuristic evidence classifier (app.evidence.links)."""

from app.evidence.links import _classify_heuristic


def test_high_similarity_supports():
    rel, strength = _classify_heuristic("Postgres handles joins well.", 0.72)
    assert rel == "supports"
    assert strength == 0.72


def test_contrast_cue_contradicts():
    rel, _ = _classify_heuristic("Actually, SQLite is not suitable for that.", 0.45)
    assert rel == "contradicts"


def test_causal_cue_explains():
    rel, _ = _classify_heuristic("This happens because the OOM killer reaps the process.", 0.48)
    assert rel == "explains"


def test_unrelated_gets_no_edge():
    assert _classify_heuristic("Some unrelated prose about the weather.", 0.15) is None


def test_contradiction_not_dropped_even_at_low_sim():
    assert _classify_heuristic("However this is worse than expected.", 0.33) is not None
