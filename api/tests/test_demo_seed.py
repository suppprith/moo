"""`moo demo`: the seeded corpus and the walkthrough it narrates.

Network is stubbed — what is tested here is the seeding contract (fetch once,
skip what is stored, report failures) and that the walkthrough tells the truth
about what moo did or did not flag.
"""

import sqlite3

import pytest

from app import demo
from app.db import migrate


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "moo.sqlite"
    migrate(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def _store(connection, url, doc_id):
    connection.execute(
        "INSERT INTO document (id, source_type, url) VALUES (?, 'docs', ?)", (doc_id, url)
    )
    connection.commit()


def test_every_demo_source_is_a_primary_https_url():
    urls = [url for url, _ in demo.DEMO_SOURCES]
    assert len(urls) == len(set(urls)), "a duplicate page would just be fetched twice"
    for url, why in demo.DEMO_SOURCES:
        assert url.startswith("https://"), url
        assert why and len(why) > 10, f"{url} needs a reason it is in the corpus"


def test_seed_fetches_what_is_missing(conn, monkeypatch):
    calls = []

    def fake_extract(connection, urls, **kw):
        calls.append(list(urls))
        return {"results": [{"url": u, "ok": True} for u in urls]}

    monkeypatch.setattr("app.extract.extract_urls", fake_extract)
    report = demo.seed(conn, echo=lambda *a: None)

    assert report["fetched"] == len(demo.DEMO_SOURCES)
    assert report["skipped"] == 0
    assert all(len(batch) <= demo.BATCH for batch in calls), "extract caps a call at MAX_URLS"
    assert sum(len(b) for b in calls) == len(demo.DEMO_SOURCES)


def test_seed_skips_pages_already_stored(conn, monkeypatch):
    first_url = demo.DEMO_SOURCES[0][0]
    _store(conn, first_url, 1)

    asked = []

    def fake_extract(connection, urls, **kw):
        asked.extend(urls)
        return {"results": [{"url": u, "ok": True} for u in urls]}

    monkeypatch.setattr("app.extract.extract_urls", fake_extract)
    report = demo.seed(conn, echo=lambda *a: None)

    assert report["skipped"] == 1
    assert first_url not in asked


def test_a_second_run_costs_nothing(conn, monkeypatch):
    for n, (url, _) in enumerate(demo.DEMO_SOURCES, start=1):
        _store(conn, url, n)

    def explode(*a, **kw):  # pragma: no cover - must not be reached
        raise AssertionError("re-fetched an already-stored page")

    monkeypatch.setattr("app.extract.extract_urls", explode)
    report = demo.seed(conn, echo=lambda *a: None)
    assert report == {"fetched": 0, "skipped": len(demo.DEMO_SOURCES), "failed": [],
                      "seconds": 0.0}


def test_seed_reports_a_page_it_could_not_fetch(conn, monkeypatch):
    def fake_extract(connection, urls, **kw):
        """First page of each batch 403s; the rest of the batch still lands."""
        return {
            "results": [{"url": urls[0], "ok": False, "error": {"code": "upstream_error"}}]
            + [{"url": u, "ok": True} for u in urls[1:]]
        }

    monkeypatch.setattr("app.extract.extract_urls", fake_extract)
    report = demo.seed(conn, echo=lambda *a: None)
    assert len(report["failed"]) >= 1
    assert report["failed"][0].startswith("https://")


def test_sources_are_listed_once_per_document():
    rows = [
        {"document_url": "https://a", "url_anchor": "https://a#one", "title": "A"},
        {"document_url": "https://a", "url_anchor": "https://a#two", "title": "A"},
        {"document_url": "https://b", "url_anchor": "https://b#one", "title": "B"},
    ]
    kept = demo._one_row_per_document(rows)
    assert [r["url_anchor"] for r in kept] == ["https://a#one", "https://b#one"]


def test_the_walkthrough_admits_when_nothing_was_flagged():
    note = demo._staleness_note({"claims": [{"text": "x"}], "sources": [{"url": "u"}]})
    assert "Nothing was flagged" in note
    assert "moo setup" in note, "say how to get a better run, not just that this one was thin"


def test_the_walkthrough_reports_what_was_flagged():
    result = {
        "claims": [{"text": "old", "disputed": True}, {"text": "older", "superseded_by": "clm_9"}],
        "sources": [{"url": "u", "version_outdated": True}],
    }
    note = demo._staleness_note(result)
    assert "1 disputed claim(s)" in note
    assert "1 superseded claim(s)" in note
    assert "older version" in note


def test_render_shows_the_answer_claims_and_sources():
    result = {
        "_seconds": 1.2,
        "answer": "SQLite is fine until it isn't [S1]",
        "claims": [{"text": "SQLite handles modest write loads", "confidence": 0.78}],
        "sources": [{"document_url": "https://www.sqlite.org/whentouse.html",
                     "title": "When To Use", "trust_score": 0.9}],
    }
    out = demo.render("when should I use SQLite instead of Postgres", result)
    assert "when should I use SQLite instead of Postgres" in out
    assert "SQLite is fine until it isn't [S1]" in out
    assert "(78%)" in out
    assert "trust 0.90" in out


def test_cli_routes_demo_to_the_walkthrough(monkeypatch):
    from app import cli

    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("app.demo.main", fake_main)
    assert cli.main(["demo", "--seed-only"]) == 0
    assert seen["argv"] == ["--seed-only"]
