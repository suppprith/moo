"""Version awareness (app.versions, SUP-136)."""

import sqlite3
from dataclasses import dataclass, field

import pytest

from app import search as search_mod
from app import versions as V

# -- extraction ---------------------------------------------------------------------

def _by_key(text):
    return {(m.product, m.version): m.relation for m in V.extract_mentions(text)}


def test_bare_mention():
    got = _by_key("PostgreSQL 16 introduces incremental sorting improvements.")
    assert got[("postgresql", "16")] == "mentions"


def test_alias_canonicalized():
    got = _by_key("Postgres 15 changed the default permissions of the public schema.")
    assert ("postgresql", "15") in got


def test_since_relation():
    got = _by_key("Parallel vacuuming was introduced in PostgreSQL 13.")
    assert got[("postgresql", "13")] == "since"


def test_deprecated_relation():
    got = _by_key("The distutils module is deprecated in Python 3.10.")
    assert got[("python", "3.10")] == "deprecated"


def test_removed_relation():
    got = _by_key("distutils was removed in Python 3.12 entirely.")
    assert got[("python", "3.12")] == "removed"


def test_relation_wins_over_bare_mention():
    text = "Python 3.12 dropped distutils. It was removed in Python 3.12."
    assert _by_key(text)[("python", "3.12")] == "removed"


def test_multiple_products():
    got = _by_key("Works with Redis 7 and Node 20, deprecated in Django 4.0.")
    assert got[("redis", "7")] == "mentions"
    assert got[("node", "20")] == "mentions"
    assert got[("django", "4.0")] == "deprecated"


def test_no_versions_no_mentions():
    assert V.extract_mentions("Indexes speed up query performance considerably.") == []


# -- query constraint ---------------------------------------------------------------

def test_query_constraint_parsed():
    c = V.query_constraint("how does json work in redis 7")
    assert c.product == "redis" and c.version == "7"


def test_query_without_version_is_none():
    assert V.query_constraint("why is my query slow") is None


# -- matching -----------------------------------------------------------------------

def test_minor_satisfies_major():
    c = V.Mention("postgresql", "16", "mentions")
    assert V.matches(V.Mention("postgresql", "16.2", "mentions"), c)
    assert not V.matches(V.Mention("postgresql", "15", "mentions"), c)
    assert not V.matches(V.Mention("redis", "16", "mentions"), c)


def test_annotate_outdated_only_older_majors():
    c = V.Mention("postgresql", "16", "mentions")
    old = [V.Mention("postgresql", "9.4", "mentions")]
    assert V.annotate(c, old) == {"match": False, "outdated": True}
    current = [V.Mention("postgresql", "16.1", "since")]
    assert V.annotate(c, current)["match"] is True
    unrelated = [V.Mention("redis", "7", "mentions")]
    assert V.annotate(c, unrelated) == {"match": False, "outdated": False}


# -- retrieval boost end-to-end (patched retrieve) ------------------------------------

@dataclass
class Hit:
    chunk_id: int
    text: str
    score: float = 0.5
    heading: str | None = None
    url_anchor: str = "#a"
    source_type: str = "docs"
    document_url: str = "http://d"
    title: str | None = "T"
    published_at: str | None = None
    author_role: str | None = None
    popularity: int | None = None
    trust_score: float | None = 0.9
    fetched_at: str | None = None
    suspicious: bool = False
    alternates: list = field(default_factory=list)


NEW_HIT = Hit(1, "Parallel vacuuming was introduced in PostgreSQL 16 and speeds cleanup.", 0.50)
OLD_HIT = Hit(2, "In PostgreSQL 9.4 the vacuum process is single-threaded only.", 0.52)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def test_version_query_reorders_and_annotates(conn, monkeypatch):
    # RRF order puts the outdated 9.4 source FIRST (it scored higher)
    monkeypatch.setattr(
        search_mod, "retrieve",
        lambda *a, **k: [Hit(**OLD_HIT.__dict__), Hit(**NEW_HIT.__dict__)],
    )
    out = search_mod.search(conn, "postgres 16 parallel vacuum", mode="raw", live=False)
    # the 16-matching source outranks the higher-RRF 9.4 source
    assert [s["chunk_id"] for s in out["sources"]] == [1, 2]
    by_id = {s["chunk_id"]: s for s in out["sources"]}
    assert by_id[1]["version_match"] is True
    assert by_id[2]["version_outdated"] is True
    assert by_id[1]["versions"][0]["relation"] == "since"
    assert out["meta"]["version_constraint"] == {"product": "postgresql", "version": "16"}


def test_no_constraint_no_version_fields(conn, monkeypatch):
    monkeypatch.setattr(
        search_mod, "retrieve",
        lambda *a, **k: [Hit(**OLD_HIT.__dict__), Hit(**NEW_HIT.__dict__)],
    )
    out = search_mod.search(conn, "how does vacuum work", mode="raw", live=False)
    # RRF order untouched, no version keys anywhere
    assert [s["chunk_id"] for s in out["sources"]] == [2, 1]
    assert all("version_match" not in s for s in out["sources"])
    assert "version_constraint" not in out["meta"]


def test_agent_format_carries_version_flags(conn, monkeypatch):
    monkeypatch.setattr(
        search_mod, "retrieve",
        lambda *a, **k: [Hit(**OLD_HIT.__dict__), Hit(**NEW_HIT.__dict__)],
    )
    out = search_mod.search(conn, "postgres 16 parallel vacuum", mode="raw",
                            format="agent", live=False)
    flags = {s["id"]: s.get("version_match") for s in out["sources"]}
    assert True in flags.values()
