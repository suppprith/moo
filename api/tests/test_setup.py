"""First-run setup: env file handling, config snippet, and the load order.

The load-order test is the one that matters. A key sitting in `api/.env` used
to be invisible to anything that ran before the first LLM call, so live search
stayed off while the startup hint told you to set the variable you had set.
"""

import json

from app import cli, env, setup_wizard


def test_parse_env_handles_comments_quotes_and_export():
    parsed = env.parse_env(
        """
        # a comment
        MOO_SEARXNG_URL=http://localhost:8888
        export MOO_BRAVE_API_KEY="BSA-secret"
        EMPTY=
        not a pair
        """
    )
    assert parsed["MOO_SEARXNG_URL"] == "http://localhost:8888"
    assert parsed["MOO_BRAVE_API_KEY"] == "BSA-secret"
    assert parsed["EMPTY"] == ""
    assert "not a pair" not in parsed


def test_load_dotenv_never_overrides_the_real_environment(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("MOO_SEARXNG_URL=http://from-file\nMOO_BRAVE_API_KEY=from-file\n")
    monkeypatch.setenv("MOO_SEARXNG_URL", "http://from-shell")
    monkeypatch.delenv("MOO_BRAVE_API_KEY", raising=False)

    env.load_dotenv(path)
    assert env.os.environ["MOO_SEARXNG_URL"] == "http://from-shell"
    assert env.os.environ["MOO_BRAVE_API_KEY"] == "from-file"


def test_env_file_is_read_before_anything_asks_what_is_configured(tmp_path, monkeypatch):
    """The first-run trap: a provider configured in the file must be live by
    the time the first hint or the first search looks for it."""
    path = tmp_path / ".env"
    path.write_text("MOO_SEARXNG_URL=http://localhost:8888\n")
    for name in ("MOO_SEARXNG_URL", "MOO_BRAVE_API_KEY", "MOO_SEARCH_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(env, "DEFAULT_ENV_PATH", path)
    monkeypatch.setattr(env, "_loaded", False)

    from app.bootstrap import ensure_ready
    from app.live.providers import resolve_provider

    assert resolve_provider() is None  # nothing has read the file yet
    result = ensure_ready(tmp_path / "moo.sqlite", quiet=True)
    assert resolve_provider() is not None, "ensure_ready must load the env file first"
    assert not any("live web search is OFF" in h for h in result["hints"])


def test_write_env_file_updates_in_place_and_keeps_comments(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# my notes\nMOO_SEARXNG_URL=http://old\nMOO_PRELOAD=1\n")

    setup_wizard.write_env_file(
        {"MOO_SEARXNG_URL": "http://new", "GEMINI_API_KEY": "k"}, path
    )
    text = path.read_text(encoding="utf-8")

    assert "# my notes" in text
    assert "MOO_PRELOAD=1" in text
    assert "MOO_SEARXNG_URL=http://new" in text
    assert "http://old" not in text
    assert "GEMINI_API_KEY=k" in text
    assert setup_wizard.read_env_file(path)["MOO_SEARXNG_URL"] == "http://new"


def test_write_env_file_creates_a_missing_file(tmp_path):
    path = setup_wizard.write_env_file({"GEMINI_API_KEY": "k"}, tmp_path / "sub" / ".env")
    assert path.exists() and "GEMINI_API_KEY=k" in path.read_text(encoding="utf-8")


def test_mask_shows_enough_to_recognise_and_not_enough_to_use():
    assert setup_wizard.mask("BSA-abcdefghijklmnop") == "BSA-******op"
    assert setup_wizard.mask("short") == "*****"


def test_mcp_snippet_names_this_checkout():
    snippet = setup_wizard.mcp_snippet()
    assert snippet.startswith("claude mcp add moo -- uv run --directory")
    assert "app.mcp_server" in snippet

    parsed = json.loads(setup_wizard.mcp_json())
    assert parsed["mcpServers"]["moo"]["command"] == "uv"
    assert "app.mcp_server" in parsed["mcpServers"]["moo"]["args"]


def test_print_config_is_the_only_output(capsys):
    assert setup_wizard.main(["--print-config"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("claude mcp add moo")


def test_status_reports_off_when_nothing_is_configured(capsys, monkeypatch):
    for name in ("MOO_SEARXNG_URL", "MOO_BRAVE_API_KEY", "MOO_LLM_PROVIDER",
                 "MOO_LLM_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(setup_wizard, "read_env_file", lambda *a, **k: {})

    assert setup_wizard.main(["--status"]) == 0
    out = capsys.readouterr().out
    assert "search provider: off" in out and "llm: off" in out


def test_setup_writes_nothing_when_every_answer_is_skipped(tmp_path, capsys):
    path = tmp_path / ".env"
    result = setup_wizard.run_setup(env_path=path, interactive=False)
    assert result["written"] == {}
    assert not path.exists()
    assert "claude mcp add moo" in capsys.readouterr().out


def test_cli_routes_setup_to_the_wizard(capsys):
    assert cli.main(["setup", "--print-config"]) == 0
    assert "claude mcp add moo" in capsys.readouterr().out
