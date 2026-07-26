"""Typed query operators."""

import sqlite3
from dataclasses import dataclass, field

import pytest

from app import queryops
from app import search as search_mod
from app.queryops import matches, parse


def test_plain_query_is_untouched():
    p = parse("how does postgres vacuum work")
    assert p.text == "how does postgres vacuum work"
    assert not p.active and not p.invalid


def test_type_operator_maps_to_source_types():
    p = parse("type:docs,so vacuum tuning")
    assert p.source_types == ["docs", "so"]
    assert p.text == "vacuum tuning"


def test_type_aliases():
    assert parse("type:issue x").source_types == ["github_issue"]
    assert parse("type:pr x").source_types == ["github_pr"]
    assert set(parse("type:github x").source_types) >= {"github_issue", "github_pr"}


def test_invalid_type_fails_soft():
    p = parse("type:banana vacuum")
    assert p.source_types is None
    assert "type:banana" in p.invalid
    assert "type:banana" in p.text


def test_since_year_month_day():
    assert parse("since:2024 x").since == "2024-01-01"
    assert parse("since:2024-05 x").since == "2024-05-01"
    assert parse("since:2024-05-02 x").since == "2024-05-02"


def test_invalid_since_fails_soft():
    p = parse("since:soon x")
    assert p.since is None and "since:soon" in p.invalid and "since:soon" in p.text


def test_site_operator_strips_www():
    p = parse("site:www.github.com wal mode")
    assert p.site == "github.com" and p.text == "wal mode"


def test_quoted_phrase_required_and_kept_in_text():
    p = parse('sqlite "write-ahead log" tuning')
    assert p.phrases == ["write-ahead log"]
    assert p.text == "sqlite write-ahead log tuning"


def test_exclusions_but_not_flags_or_signals():
    p = parse("kill -9 postgres --force -windows")
    assert p.excludes == ["windows"]
    assert "-9" in p.text and "--force" in p.text


def test_lang_folds_into_text():
    p = parse("lang:python orm comparison")
    assert p.text == "python orm comparison"
    assert not p.active


def test_unknown_operator_is_text():
    p = parse("re:invent recap")
    assert "re:invent" in p.text and "re:invent" in p.invalid


def test_operator_inside_quotes_is_literal():
    p = parse('"type:docs is an operator"')
    assert p.source_types is None
    assert p.phrases == ["type:docs is an operator"]


def test_matches_site_suffix():
    p = parse("site:github.com x")
    assert matches(p, text="t", url="https://github.com/a/b")
    assert matches(p, text="t", url="https://gist.github.com/a")
    assert not matches(p, text="t", url="https://notgithub.com/a")


def test_matches_phrase_and_exclusion():
    p = parse('"dead tuples" -mysql x')
    assert matches(p, text="vacuum reclaims dead tuples", url="u")
    assert not matches(p, text="vacuum reclaims dead rows", url="u")
    assert not matches(p, text="mysql handles dead tuples", url="u")


@dataclass
class FakeHit:
    chunk_id: int
    score: float = 0.5
    text: str = "text"
    heading: str | None = "H"
    url_anchor: str = "#a"
    source_type: str = "docs"
    document_url: str = "http://d"
    title: str | None = "T"
    published_at: str | None = None
    trust_score: float | None = 0.9
    alternates: list = field(default_factory=list)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def test_search_passes_filters_to_retrieve(conn, monkeypatch):
    seen = {}

    def fake_retrieve(conn, query, **kw):
        seen["query"] = query
        seen.update(kw)
        return [FakeHit(1)]

    monkeypatch.setattr(search_mod, "retrieve", fake_retrieve)
    out = search_mod.search(conn, "type:so since:2024 vacuum tuning", mode="raw")
    assert seen["source_types"] == ["so"] and seen["since"] == "2024-01-01"
    assert seen["query"] == "vacuum tuning"
    assert out["query"] == "type:so since:2024 vacuum tuning"
    assert out["meta"]["operators"] == {"source_types": ["so"], "since": "2024-01-01"}


def test_search_post_filters_site_and_exclusion(conn, monkeypatch):
    hits = [
        FakeHit(1, document_url="https://github.com/x", url_anchor="https://github.com/x#i"),
        FakeHit(2, document_url="https://stackoverflow.com/q", url_anchor="https://stackoverflow.com/q"),
        FakeHit(3, document_url="https://github.com/y", url_anchor="https://github.com/y",
                text="only relevant on windows"),
    ]
    monkeypatch.setattr(search_mod, "retrieve", lambda *a, **k: list(hits))
    out = search_mod.search(conn, "site:github.com -windows wal", mode="raw")
    assert [s["chunk_id"] for s in out["sources"]] == [1]


def test_search_overfetches_for_post_filters(conn, monkeypatch):
    seen = {}

    def fake_retrieve(conn, query, *, k, **kw):
        seen["k"] = k
        return []

    monkeypatch.setattr(search_mod, "retrieve", fake_retrieve)
    search_mod.search(conn, "wal mode", mode="raw", k=10)
    plain_k = seen["k"]
    search_mod.search(conn, 'site:sqlite.org "wal mode"', mode="raw", k=10)
    assert seen["k"] > plain_k


def test_search_invalid_operator_hint_in_meta(conn, monkeypatch):
    monkeypatch.setattr(search_mod, "retrieve", lambda *a, **k: [FakeHit(1)])
    out = search_mod.search(conn, "type:banana vacuum", mode="raw")
    assert out["meta"]["operators"]["invalid"] == ["type:banana"]
