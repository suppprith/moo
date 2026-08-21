"""``moo setup`` — first run, in one command.

Getting from "interested" to "answering my agent's queries" used to mean:
clone, install uv, sync, find a search provider, find an LLM key, learn two
variable names, write a dotenv file by hand, then work out the MCP config. Most
of that is now automatic (the database migrates itself on first use); what was
left is the two optional keys and the client snippet, which is what this asks
for and writes.

Design rules, in case this grows:

- **Skippable.** Both keys are optional and moo runs without them — every
  prompt takes empty input as "not now" and says what that costs.
- **Never clobbers.** An existing value is shown masked and kept unless the
  answer replaces it; the rest of the file is preserved.
- **Secrets stay put.** Values are written to ``api/.env`` (gitignored) and
  never echoed back in full, printed to logs, or sent anywhere.
- **Non-interactive callers are first class.** ``--print-config`` emits the MCP
  snippet and nothing else, so scripts and docs quote one source of truth.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from .env import DEFAULT_ENV_PATH, load_dotenv, parse_env

SEARCH_KEYS = ("MOO_SEARXNG_URL", "MOO_BRAVE_API_KEY")
LLM_KEYS = ("MOO_LLM_PROVIDER", "MOO_LLM_API_KEY", "GEMINI_API_KEY")

_PROVIDERS = {
    "1": ("searxng", "self-hosted SearXNG (keyless)"),
    "2": ("brave", "Brave Search API (free tier)"),
    "3": (None, "skip — answer from the local store only"),
}
_LLM_PROVIDERS = {
    "1": ("gemini", "GEMINI_API_KEY"),
    "2": ("openai", "MOO_LLM_API_KEY"),
    "3": ("anthropic", "MOO_LLM_API_KEY"),
    "4": ("ollama", None),
    "5": (None, None),
}


def mask(value: str) -> str:
    """Show enough to recognise a value, never enough to use it."""
    value = value.strip()
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 6}{value[-2:]}"


def read_env_file(path: Path | str = DEFAULT_ENV_PATH) -> dict[str, str]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return parse_env(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def write_env_file(values: dict[str, str], path: Path | str = DEFAULT_ENV_PATH) -> Path:
    """Merge ``values`` into the env file, preserving comments and order.

    Rewriting the file from a dict would throw away whatever the user wrote
    around their keys, so existing lines are edited in place and only genuinely
    new names are appended."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)

    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip() if "=" in stripped else ""
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not stripped.startswith("#") and key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# written by `moo setup`")
        out += [f"{key}={value}" for key, value in remaining.items()]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def mcp_snippet(api_dir: Path | None = None) -> str:
    """The `claude mcp add` line for this checkout, with the real path in it."""
    directory = str(api_dir or Path(__file__).resolve().parent.parent)
    return f'claude mcp add moo -- uv run --directory "{directory}" python -m app.mcp_server'


def mcp_json(api_dir: Path | None = None) -> str:
    """The same server as a `claude_desktop_config.json` fragment."""
    directory = str(api_dir or Path(__file__).resolve().parent.parent)
    return json.dumps(
        {
            "mcpServers": {
                "moo": {
                    "command": "uv",
                    "args": ["run", "--directory", directory, "python", "-m", "app.mcp_server"],
                }
            }
        },
        indent=2,
    )


def status(existing: dict[str, str] | None = None) -> dict:
    """What is configured right now, reading the env file and the environment."""
    load_dotenv()
    existing = existing if existing is not None else read_env_file()

    def value(key: str) -> str | None:
        return os.environ.get(key) or existing.get(key) or None

    search = next(((k, value(k)) for k in SEARCH_KEYS if value(k)), (None, None))
    llm_key = next((k for k in LLM_KEYS if value(k)), None)
    return {
        "search_provider": search[0],
        "search_value": search[1],
        "llm": value("MOO_LLM_PROVIDER") or ("gemini" if value("GEMINI_API_KEY") else None),
        "llm_key_name": llm_key,
    }


def _ask(prompt: str, *, default: str = "") -> str:
    try:
        return input(prompt).strip() or default
    except EOFError:  # piped stdin: take the defaults and move on
        return default


def _ask_secret(prompt: str) -> str:
    """Read a key without echoing it to the terminal or the scrollback."""
    try:
        return getpass.getpass(prompt).strip()
    except (EOFError, getpass.GetPassWarning):
        return ""


def _ask_search(existing: dict[str, str], out: dict[str, str]) -> None:
    current = next((k for k in SEARCH_KEYS if existing.get(k)), None)
    print("\n1. Live web search — what moo uses to discover pages.")
    if current:
        print(f"   currently: {current}={mask(existing[current])}")
    for option, (_, label) in _PROVIDERS.items():
        print(f"   {option}) {label}")
    choice = _ask("   choose [3]: ", default="3")
    provider = _PROVIDERS.get(choice, _PROVIDERS["3"])[0]

    if provider == "searxng":
        url = _ask("   SearXNG URL [http://localhost:8888]: ", default="http://localhost:8888")
        out["MOO_SEARXNG_URL"] = url
        print("   note: enable `json` under search.formats in its settings.yml")
    elif provider == "brave":
        key = _ask_secret("   Brave API key (not echoed): ")
        if key:
            out["MOO_BRAVE_API_KEY"] = key
        else:
            print("   nothing entered — leaving live search off")
    else:
        print("   skipped: moo answers from pages it has already fetched.")


def _ask_llm(existing: dict[str, str], out: dict[str, str]) -> None:
    print("\n2. LLM — sharpens query expansion, claim extraction and the written answer.")
    current = next((k for k in LLM_KEYS if existing.get(k)), None)
    if current:
        print(f"   currently: {current}={mask(existing[current])}")
    print("   1) Gemini   2) OpenAI   3) Anthropic   4) Ollama (local, keyless)   5) skip")
    choice = _ask("   choose [5]: ", default="5")
    provider, key_name = _LLM_PROVIDERS.get(choice, _LLM_PROVIDERS["5"])

    if provider is None:
        print("   skipped: every LLM stage falls back to a deterministic heuristic.")
        return
    if provider == "ollama":
        out["MOO_LLM_PROVIDER"] = "ollama"
        out["MOO_LLM_MODEL"] = _ask("   model [llama3.1]: ", default="llama3.1")
        return

    key = _ask_secret(f"   {provider} API key (not echoed): ")
    if not key:
        print("   nothing entered — leaving the heuristic fallbacks in place")
        return
    out[key_name] = key
    if provider != "gemini":
        out["MOO_LLM_PROVIDER"] = provider


def run_setup(*, env_path: Path | str = DEFAULT_ENV_PATH, interactive: bool = True) -> dict:
    """Walk the two optional settings, write them, and print the client config."""
    env_path = Path(env_path)
    existing = read_env_file(env_path)

    print("moo setup — both questions are optional; press Enter to skip either.")
    values: dict[str, str] = {}
    if interactive:
        _ask_search(existing, values)
        _ask_llm(existing, values)

    if values:
        written = write_env_file(values, env_path)
        print(f"\nwrote {', '.join(sorted(values))} to {written}")
    else:
        print("\nnothing to write — moo runs keyless, from its local store.")

    print("\n3. Connect your agent:\n")
    print(f"   {mcp_snippet()}\n")
    print("   Then ask it something, or try it here first:\n")
    print('   uv run moo "how does sqlite wal mode work"\n')
    return {"written": values, "path": str(env_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="moo setup", description="Configure moo and print the client snippet."
    )
    parser.add_argument("--print-config", action="store_true",
                        help="print the MCP client config and exit")
    parser.add_argument("--json", action="store_true",
                        help="with --print-config, emit the JSON form")
    parser.add_argument("--status", action="store_true",
                        help="report what is configured and exit")
    parser.add_argument("--env-path", default=str(DEFAULT_ENV_PATH), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    from .cli import use_utf8_stdout

    use_utf8_stdout()

    if args.print_config:
        print(mcp_json() if args.json else mcp_snippet())
        return 0

    if args.status:
        state = status()
        search = state["search_provider"] or "off (local store only)"
        llm = state["llm"] or "off (heuristic fallbacks)"
        print(f"search provider: {search}\nllm: {llm}\nenv file: {DEFAULT_ENV_PATH}")
        return 0

    run_setup(env_path=args.env_path, interactive=sys.stdin.isatty())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
