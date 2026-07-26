"""Software-domain source policy."""

from app.live.policy import (
    OFF_DOMAIN_THRESHOLD,
    classify,
    domain_confidence,
    out_of_domain,
    rank_candidates,
)
from app.live.providers import Candidate


def test_official_docs_top_tier():
    d = classify("https://docs.python.org/3/library/asyncio.html")
    assert d.tier == "docs" and d.prior == 1.0 and d.source_type == "docs"


def test_www_prefix_stripped():
    assert classify("https://www.postgresql.org/docs/16/mvcc.html").tier == "docs"


def test_suffix_family_readthedocs():
    assert classify("https://sqlalchemy.readthedocs.io/en/latest/").tier == "docs"


def test_suffix_family_stackexchange():
    d = classify("https://dba.stackexchange.com/questions/1")
    assert d.tier == "qa" and d.source_type == "so"


def test_github_paths_refine_source_type():
    assert classify("https://github.com/postgres/postgres/issues/99").source_type == "github_issue"
    assert classify("https://github.com/redis/redis/pull/123").source_type == "github_pr"
    assert classify("https://github.com/redis/redis").source_type == "docs"


def test_unknown_domain_low_prior_not_blocked():
    d = classify("https://random-food-blog.example.com/best-pizza")
    assert d.tier == "unknown" and 0 < d.prior < OFF_DOMAIN_THRESHOLD


def test_blocked_hosts():
    assert classify("https://www.youtube.com/watch?v=x").tier == "blocked"
    assert classify("https://x.com/someone/status/1").tier == "blocked"


def test_rank_prefers_docs_over_unknown_despite_provider_rank():
    cands = [
        Candidate("https://seo-spam.example.com/postgres", rank=0),
        Candidate("https://www.postgresql.org/docs/16/routine-vacuuming.html", rank=1),
        Candidate("https://www.youtube.com/watch?v=abc", rank=2),
    ]
    ranked = rank_candidates(cands, max_pages=5)
    urls = [c.url for c, _ in ranked]
    assert urls[0].startswith("https://www.postgresql.org")
    assert all("youtube" not in u for u in urls)


def test_rank_caps_at_max_pages():
    cands = [Candidate(f"https://docs.python.org/3/{i}", rank=i) for i in range(10)]
    assert len(rank_candidates(cands, max_pages=3)) == 3


DEV_CANDS = [
    Candidate("https://stackoverflow.com/questions/1", rank=0),
    Candidate("https://docs.python.org/3/library/gc.html", rank=1),
    Candidate("https://github.com/python/cpython/issues/5", rank=2),
    Candidate("https://realpython.com/python-gc/", rank=3),
]

FOOD_CANDS = [
    Candidate("https://tasty.example.com/pizza", rank=0),
    Candidate("https://recipes.example.org/dough", rank=1),
    Candidate("https://foodnetwork.example.net/best", rank=2),
]


def test_software_query_in_domain():
    assert domain_confidence(DEV_CANDS) >= OFF_DOMAIN_THRESHOLD
    assert not out_of_domain(DEV_CANDS)


def test_non_software_query_out_of_domain():
    assert out_of_domain(FOOD_CANDS)


def test_empty_discovery_is_out_of_domain():
    assert out_of_domain([])
