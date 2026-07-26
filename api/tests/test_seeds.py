"""Multi-domain seed registry."""

import pytest

from app.ingest import seeds


def test_multiple_verticals_registered():
    names = set(seeds.list_verticals())
    assert "databases" in names
    assert {"languages", "web-frameworks", "cloud-infra"} <= names
    assert len(names) >= 5


def test_unions_span_all_verticals():
    assert "postgres/postgres" in seeds.GITHUB_REPOS
    assert "fastapi/fastapi" in seeds.GITHUB_REPOS
    kw = set(seeds.DOMAIN_KEYWORDS)
    assert {"postgres", "python", "kubernetes"} <= kw


def test_is_domain_relevant_spans_verticals():
    assert seeds.is_domain_relevant("how does the python gil work")
    assert seeds.is_domain_relevant("postgres deadlock on update")
    assert seeds.is_domain_relevant("kubernetes pod networking")
    assert not seeds.is_domain_relevant("best sourdough bread recipe")


def test_get_vertical_and_unknown():
    v = seeds.get_vertical("web-frameworks")
    assert "fastapi/fastapi" in v.github_repos and v.stackoverflow_tags
    with pytest.raises(KeyError):
        seeds.get_vertical("nonexistent")


def test_databases_vertical_is_deep():
    db = seeds.get_vertical("databases")
    assert len(db.github_repos) >= 5 and db.docs_sites
