"""First-run self-initialization.

``uvx moo-mcp`` (or the API) must go from nothing to working with zero manual
setup: ``ensure_ready()`` creates the database and applies pending migrations
idempotently, then reports friendly configuration hints for the optional
pieces (live search provider, LLM key) instead of failing on their absence —
moo degrades to store-only + heuristics, and the hints say how to level up.

Called by both entrypoints (moo-mcp main, FastAPI startup). Never raises for
a missing optional config; only a truly broken DB path can fail.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .db import DEFAULT_DB_PATH, migrate

log = logging.getLogger("moo.bootstrap")


def config_hints() -> list[str]:
    """Human-readable hints for optional config that isn't set."""
    hints: list[str] = []
    from .live.providers import resolve_provider

    if resolve_provider() is None:
        hints.append(
            "live web search is OFF (store/cache only) — set MOO_SEARXNG_URL "
            "(keyless, self-hosted) or MOO_BRAVE_API_KEY; see api/.env.example"
        )
    from . import llm

    if llm.get_client() is None:
        hints.append(
            "no LLM key — claims/synthesis run on heuristics; set GEMINI_API_KEY "
            "or MOO_LLM_PROVIDER + MOO_LLM_API_KEY (BYOK); see api/.env.example"
        )
    return hints


def preload_enabled() -> bool:
    """Whether to load models at boot instead of on the first request. Off by
    default (a CLI run should not pay for it), on in the container image."""
    return os.environ.get("MOO_PRELOAD", "").strip().lower() in ("1", "true", "yes", "on")


def warm_models(model_name: str | None = None) -> bool:
    """Load the embedding model up front so the first search does not pay the
    cold start. Best effort: a failure here must never stop the server."""
    try:
        from .embed import DEFAULT_MODEL, get_model

        get_model(model_name or DEFAULT_MODEL)
        log.info("embedding model warm")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("could not preload the embedding model: %s", exc)
        return False


def ensure_ready(db_path: Path | str = DEFAULT_DB_PATH, *, quiet: bool = False) -> dict:
    """Create/migrate the database and surface config hints. Idempotent —
    safe to call on every start. Returns {migrations_applied, hints}."""
    applied = migrate(db_path)
    hints = config_hints()
    if not quiet:
        if applied:
            log.info("applied %d migration(s)", applied)
        for hint in hints:
            log.warning("%s", hint)
    return {"migrations_applied": applied, "hints": hints}
