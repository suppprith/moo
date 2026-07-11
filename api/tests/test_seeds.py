"""Multi-domain seed registry (app.ingest.seeds, SUP-125)."""

import pytest

from app.ingest import seeds


def test_multiple_verticals_registered():
    names = set(seeds.list_verticals())
    assert "databases" in names
    assert {"languages", "web-frameworks", "cloud-infra"} <= names
    assert len(names) >= 5


def test_unions_span_all_verticals():
    # github repos union covers databases + a newer vertical
    assert "postgres/postgres" in seeds.GITHUB_REPOS
    assert "fastapi/fastapi" in seeds.GITHUB_REPOS
    # keyword union spans domains
    kw = set(seeds.DOMAIN_KEYWORDS)
    assert {"postgres", "python", "kubernetes"} <= kw


def test_is_domain_relevant_spans_verticals():
    assert seeds.is_domain_relevant("how does the python gil work")     # languages
    assert seeds.is_domain_relevant("postgres deadlock on update")      # databases
    assert seeds.is_domain_relevant("kubernetes pod networking")        # cloud-infra
    assert not seeds.is_domain_relevant("best sourdough bread recipe")  # off-topic


def test_get_vertical_and_unknown():
    v = seeds.get_vertical("web-frameworks")
    assert "fastapi/fastapi" in v.github_repos and v.stackoverflow_tags
    with pytest.raises(KeyError):
        seeds.get_vertical("nonexistent")


def test_databases_vertical_is_deep():
    db = seeds.get_vertical("databases")
    # v1 stays the deep proof: most repos + docs live here
    assert len(db.github_repos) >= 5 and db.docs_sites
