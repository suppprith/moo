"""First-run self-initialization."""

import sqlite3

import pytest

from app.bootstrap import config_hints, ensure_ready, preload_enabled, warm_models

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
    from app import llm

    llm._reset_config()
    yield
    llm._reset_config()


def test_ensure_ready_bootstraps_fresh_db(tmp_path):
    db = tmp_path / "fresh" / "moo.sqlite"
    out = ensure_ready(db, quiet=True)
    assert out["migrations_applied"] >= 6
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"document", "chunk", "claim", "claim_link", "research_run"} <= tables


def test_ensure_ready_idempotent(tmp_path):
    db = tmp_path / "moo.sqlite"
    ensure_ready(db, quiet=True)
    out = ensure_ready(db, quiet=True)
    assert out["migrations_applied"] == 0


def test_hints_when_unconfigured():
    hints = " ".join(config_hints())
    assert "MOO_SEARXNG_URL" in hints
    assert "MOO_LLM_PROVIDER" in hints


def test_hints_clear_when_configured(monkeypatch):
    monkeypatch.setenv("MOO_BRAVE_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    from app import llm

    llm._reset_config()
    assert config_hints() == []


def test_migrate_prints_nothing_to_stdout(tmp_path, capsys):
    ensure_ready(tmp_path / "moo.sqlite", quiet=True)
    assert capsys.readouterr().out == ""


def test_preload_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv("MOO_PRELOAD", raising=False)
    assert preload_enabled() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("MOO_PRELOAD", value)
        assert preload_enabled() is True
    monkeypatch.setenv("MOO_PRELOAD", "0")
    assert preload_enabled() is False


def test_warm_models_never_takes_the_server_down(monkeypatch):
    from app import embed

    monkeypatch.setattr(embed, "get_model", lambda name: (_ for _ in ()).throw(OSError("no disk")))
    assert warm_models() is False


def test_warm_models_loads_the_configured_model(monkeypatch):
    from app import embed

    loaded = []
    monkeypatch.setattr(embed, "get_model", loaded.append)
    assert warm_models("stub-model") is True
    assert loaded == ["stub-model"]
