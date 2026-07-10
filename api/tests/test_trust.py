"""Unit tests for source trust scoring (app.evidence.trust)."""

from datetime import UTC, datetime

from app.evidence.trust import recency_multiplier, trust_score

NOW = datetime(2026, 7, 10, tzinfo=UTC)


def score(**doc):
    return trust_score(doc, NOW)[0]


def test_tier_ordering():
    # rubric ordering holds at equal recency: official docs > maintainer comment
    # > engineering blog > accepted SO answer > forum post
    d = "2026-06-15"  # recent + uniform so base tiers, not decay, decide order
    docs = score(source_type="docs", published_at=d)
    maint = score(source_type="github_comment", author_role="maintainer", published_at=d)
    blog = score(source_type="blog", published_at=d)
    so = score(source_type="so_answer", accepted=True, published_at=d)
    reddit = score(source_type="reddit_post", published_at=d)
    assert docs > maint > blog > so > reddit


def test_maintainer_beats_none_same_source():
    maint = score(source_type="github_comment", author_role="maintainer", published_at="2025-01-01")
    anon = score(source_type="github_comment", author_role="none", published_at="2025-01-01")
    assert maint > anon


def test_accepted_answer_bonus():
    accepted = score(source_type="so_answer", accepted=True, published_at="2025-01-01")
    plain = score(source_type="so_answer", accepted=False, published_at="2025-01-01")
    assert accepted > plain


def test_recency_decay():
    fresh = score(source_type="blog", published_at="2026-06-01")
    stale = score(source_type="blog", published_at="2015-01-01")
    assert fresh > stale


def test_docs_no_date_not_penalized():
    # a docs page with no publish date tracks "current" — full recency
    assert recency_multiplier("docs", None, NOW) == 1.0
    assert recency_multiplier("blog", None, NOW) < 1.0


def test_score_bounded():
    hi = score(source_type="docs", author_role="maintainer", popularity=99999,
               accepted=True, published_at="2026-07-09")
    assert 0.0 <= hi <= 1.0


def test_unknown_source_type_gets_default():
    assert 0.0 < score(source_type="mystery", published_at="2025-01-01") <= 1.0
