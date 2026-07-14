"""First-run self-initialization (app.bootstrap, SUP-159 local path)."""

import sqlite3

import pytest

from app.bootstrap import config_hints, ensure_ready

CFG_VARS = [
    "MOO_SEARCH_PROVIDER", "MOO_SEARXNG_URL", "MOO_SEARCH_URL",
    "MOO_BRAVE_API_KEY", "MOO_SEARCH_API_KEY",
    "MOO_LLM_PROVIDER", "MOO_LLM_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in CFG_VARS:
        monkeypatch.delenv(var, raising=False)
    # llm caches its resolved config for the process — reset around each test
    from app import llm

    llm._reset_config()
    yield
    llm._reset_config()


def test_ensure_ready_bootstraps_fresh_db(tmp_path):
    db = tmp_path / "fresh" / "moo.sqlite"   # parent dir doesn't exist yet
    out = ensure_ready(db, quiet=True)
    assert out["migrations_applied"] >= 6    # full schema from nothing
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"document", "chunk", "claim", "claim_link", "research_run"} <= tables


def test_ensure_ready_idempotent(tmp_path):
    db = tmp_path / "moo.sqlite"
    ensure_ready(db, quiet=True)
    out = ensure_ready(db, quiet=True)       # second run: nothing to do, no error
    assert out["migrations_applied"] == 0


def test_hints_when_unconfigured():
    hints = " ".join(config_hints())
    assert "MOO_SEARXNG_URL" in hints        # live-search hint
    assert "MOO_LLM_PROVIDER" in hints       # LLM hint


def test_hints_clear_when_configured(monkeypatch):
    monkeypatch.setenv("MOO_BRAVE_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    from app import llm

    llm._reset_config()
    assert config_hints() == []


def test_migrate_prints_nothing_to_stdout(tmp_path, capsys):
    # stdout belongs to the MCP stdio transport — regression guard
    ensure_ready(tmp_path / "moo.sqlite", quiet=True)
    assert capsys.readouterr().out == ""
