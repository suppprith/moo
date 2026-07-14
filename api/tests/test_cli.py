"""The moo terminal client (app.cli, SUP-102)."""

import json

import pytest

from app import cli

FULL_RESPONSE = {
    "query": "q",
    "mode": "full",
    "answer": "Autovacuum triggers on dead-tuple churn [S1]; the default scale factor is 0.2 [S2].",
    "claims": [
        {"id": 1, "text": "Autovacuum triggers on churn", "confidence": 0.82, "disputed": False},
        {"id": 2, "text": "Old claim", "confidence": 0.5, "disputed": False, "superseded_by": 9},
    ],
    "citations": [
        {"index": 1, "url": "https://postgresql.org/docs/16/routine-vacuuming.html"},
        {"index": 2, "url": "https://stackoverflow.com/q/12345"},
    ],
    "sources": [
        {"title": "Routine Vacuuming", "url_anchor": "https://postgresql.org/docs/16/routine-vacuuming.html#autovacuum", "document_url": "https://postgresql.org/docs/16/routine-vacuuming.html"},
        {"title": "When does autovacuum run?", "url_anchor": "", "document_url": "https://stackoverflow.com/q/12345", "suspicious": True},
    ],
    "meta": {"live": {"fetched": 2, "fresh": 0, "out_of_domain": False}},
}

RAW_RESPONSE = {
    "query": "q", "mode": "raw", "answer": None, "claims": [],
    "citations": [], "sources": FULL_RESPONSE["sources"],
    "meta": {},
}


# -- rendering ---------------------------------------------------------------------

def test_render_full_has_answer_and_citations():
    out = cli.render(FULL_RESPONSE)
    assert out.startswith("Autovacuum triggers")
    assert "[S1] https://postgresql.org" in out
    assert "\x1b[" not in out                      # color=False -> zero ANSI


def test_render_color_only_when_asked():
    assert "\x1b[" in cli.render(FULL_RESPONSE, color=True)


def test_render_raw_lists_sources_with_flags():
    out = cli.render(RAW_RESPONSE)
    assert "1. Routine Vacuuming" in out
    assert "untrusted" in out                      # suspicious flag surfaced


def test_render_live_note():
    assert "live: 2 fetched" in cli.render(FULL_RESPONSE)


def test_render_empty():
    empty = {"query": "q", "mode": "raw", "sources": [], "claims": [], "meta": {}}
    assert "no results" in cli.render(empty)


# -- CLI behaviour -------------------------------------------------------------------

@pytest.fixture
def patched_search(monkeypatch):
    monkeypatch.setattr(cli, "_search_inprocess", lambda args: FULL_RESPONSE)


def test_json_flag_pipes_raw_response(patched_search, capsys):
    assert cli.main(["postgres autovacuum", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["answer"].startswith("Autovacuum")


def test_plain_output_no_ansi_when_piped(patched_search, capsys):
    # pytest capture is not a TTY -> pipe-friendly output
    assert cli.main(["postgres autovacuum"]) == 0
    out = capsys.readouterr().out
    assert "\x1b[" not in out and "Sources" in out


def test_open_launches_nth_source(patched_search, monkeypatch):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda u: opened.append(u))
    assert cli.main(["q", "--open", "1"]) == 0
    assert opened == ["https://postgresql.org/docs/16/routine-vacuuming.html#autovacuum"]


def test_open_out_of_range_fails(patched_search, monkeypatch, capsys):
    monkeypatch.setattr(cli.webbrowser, "open", lambda u: pytest.fail("should not open"))
    assert cli.main(["q", "--open", "99"]) == 1
    assert "no source #99" in capsys.readouterr().err


def test_search_failure_is_one_clear_line(monkeypatch, capsys):
    def boom(args):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "_search_inprocess", boom)
    assert cli.main(["q"]) == 1
    assert "search failed: kaboom" in capsys.readouterr().err


def test_url_flag_uses_http(monkeypatch, capsys):
    called = {}

    def fake_http(base_url, args):
        called["url"] = base_url
        return RAW_RESPONSE

    monkeypatch.setattr(cli, "_search_http", fake_http)
    assert cli.main(["q", "--url", "http://myserver:8000"]) == 0
    assert called["url"] == "http://myserver:8000"
