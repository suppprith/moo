"""Corroboration-fused ranking."""

from datetime import datetime, timedelta, timezone

from app.retrieve import (
    RECENCY_FLOOR,
    RetrievedChunk,
    _recency_factor,
    fuse,
)


def _hit(cid, rrf, trust=None, alternates=(), published=None):
    return RetrievedChunk(
        chunk_id=cid, score=rrf, text="t", heading=None, url_anchor="#",
        source_type="docs", document_url="http://d", title="T",
        published_at=published, author_role=None, popularity=None,
        trust_score=trust, alternates=list(alternates),
    )


def test_recency_neutral_when_undated():
    assert _recency_factor(None) == 1.0
    assert _recency_factor("not-a-date") == 1.0


def test_recency_decays_to_floor():
    old = (datetime.now(timezone.utc) - timedelta(days=365 * 12)).isoformat()
    assert _recency_factor(old) == RECENCY_FLOOR
    recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert _recency_factor(recent) > 0.99


def test_neutral_hit_score_unchanged():
    h = _hit(1, rrf=0.5)
    fuse(h)
    assert h.score == 0.5
    assert h.rank_signals["trust"] == 1.0
    assert h.rank_signals["corroboration"] == 1.0
    assert h.rank_signals["recency"] == 1.0


def test_trusted_corroborated_beats_slightly_better_match():
    strong = _hit(1, rrf=0.50, trust=0.95, alternates=[7, 8],
                  published=datetime.now(timezone.utc).isoformat())
    weak = _hit(2, rrf=0.55, trust=0.20,
                published=(datetime.now(timezone.utc) - timedelta(days=365 * 9)).isoformat())
    fuse(strong)
    fuse(weak)
    assert strong.score > weak.score


def test_relevance_still_dominates_large_gaps():
    much_better = _hit(1, rrf=0.9, trust=0.3)
    worse = _hit(2, rrf=0.3, trust=1.0, alternates=[5, 6, 7])
    fuse(much_better)
    fuse(worse)
    assert much_better.score > worse.score


def test_rank_signals_recorded():
    h = _hit(1, rrf=0.4, trust=0.9, alternates=[2])
    fuse(h)
    s = h.rank_signals
    assert s["rrf"] == 0.4
    assert s["trust"] > 1.0 and s["corroboration"] > 1.0
    assert abs(s["fused"] - h.score) < 1e-6


def test_signals_surface_on_sources():
    from app.search import _sources

    h = _hit(1, rrf=0.4, trust=0.9)
    fuse(h)
    row = _sources([h])[0]
    assert row["rank_signals"]["fused"] == round(h.score, 6)
