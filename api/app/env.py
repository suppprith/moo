"""Reading ``api/.env``.

One tiny module with no imports of its own so *everything* can load config
before it reads it. This used to live inside :mod:`app.llm` and ran only when
something asked for an LLM client, which made a whole class of first-run bug:
put ``MOO_SEARXNG_URL`` in ``api/.env``, start moo, and live search is off —
because the file had not been read yet — while the startup hint cheerfully
tells you live search is off and to set the variable you just set.

Real environment variables always win: the file only fills in what is not
already set, so a container's env or a shell export overrides it.
"""

from __future__ import annotations

import os
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = API_DIR / ".env"

_loaded = False


def parse_env(text: str) -> dict[str, str]:
    """Parse dotenv text. Pure, so the quirks are testable: ``#`` comments,
    blank lines, ``export`` prefixes, and quoted values."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_dotenv(path: Path | str | None = None, *, force: bool = False) -> int:
    """Populate ``os.environ`` from the env file. Returns how many names were
    set. Idempotent: repeat calls are free unless ``force`` is passed."""
    global _loaded
    if _loaded and not force and path is None:
        return 0
    env_path = Path(path) if path is not None else DEFAULT_ENV_PATH
    if path is None:
        _loaded = True
    if not env_path.exists():
        return 0
    try:
        values = parse_env(env_path.read_text(encoding="utf-8"))
    except OSError:
        return 0
    applied = 0
    for key, value in values.items():
        if key not in os.environ:
            os.environ[key] = value
            applied += 1
    return applied
