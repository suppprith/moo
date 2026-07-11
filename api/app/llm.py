"""Pluggable LLM provider layer (SUP-82; groundwork for SUP-104).

Every LLM stage in the pipeline goes through ``generate_json()`` so the
provider is swappable and results are cached in the ``llm_cache`` table by
stage + normalized-input hash — repeat queries never hit the API.

Active provider: **Google Gemini** (``google-genai``), cheap/fast tier for the
expansion/claims/rerank stages. Credentials resolve from ``GEMINI_API_KEY`` /
``GOOGLE_API_KEY`` (also read from ``api/.env`` for convenience); when none are
available, callers fall back to their heuristic path so retrieval always works.
``MOO_LLM_MODEL`` overrides the model. The Ollama option (SUP-104) plugs in here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import sqlite3
from pathlib import Path

log = logging.getLogger("moo.llm")

# Cheap/fast model for expansion, claims, evidence linking, rerank.
CHEAP_MODEL = os.environ.get("MOO_LLM_MODEL", "gemini-2.5-flash")

_API_DIR = Path(__file__).resolve().parent.parent
_client = None
_client_checked = False


def _load_dotenv() -> None:
    """Best-effort: populate GEMINI/GOOGLE keys from api/.env if not already set."""
    env_path = _API_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


def get_client():
    """Return a genai.Client, or None if no credentials resolve."""
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    _load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.info("no GEMINI_API_KEY/GOOGLE_API_KEY found; LLM stages use heuristic fallbacks")
        return None
    try:
        from google import genai

        _client = genai.Client(api_key=api_key)
    except Exception as exc:  # noqa: BLE001 - LLM absence must never break retrieval
        log.warning("gemini client unavailable: %s", exc)
    return _client


# Per-process cost counters (SUP-122). Reset around a run to measure it; note
# they are process-global, so a single run at a time gets clean numbers.
_STATS = {"calls": 0, "cache_hits": 0, "cache_misses": 0}


def reset_stats() -> None:
    for k in _STATS:
        _STATS[k] = 0


def get_stats() -> dict:
    return dict(_STATS)


def cache_key(stage: str, normalized_input: str) -> str:
    digest = hashlib.sha256(normalized_input.encode("utf-8")).hexdigest()
    return f"{stage}:{digest}"


def cache_get(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM llm_cache WHERE key = ?", (key,)).fetchone()
    if row:
        _STATS["cache_hits"] += 1
        return json.loads(row[0])
    _STATS["cache_misses"] += 1
    return None


def cache_put(conn: sqlite3.Connection, key: str, value, model: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO llm_cache (key, value, model) VALUES (?, ?, ?)",
        (key, json.dumps(value, ensure_ascii=False), model),
    )
    conn.commit()


def cached_json(conn: sqlite3.Connection, stage: str, key_input: str, prompt: str, *, schema: dict, **kw):
    """cache_get -> generate_json -> cache_put in one call, keyed by content, so
    every LLM stage is cached (SUP-122). A repeated identical run is a cache hit
    and makes zero API calls. Non-results (heuristic fallback) are not cached."""
    key = cache_key(stage, key_input)
    hit = cache_get(conn, key)
    if hit is not None:
        return hit
    result = generate_json(prompt, schema=schema, **kw)
    if result is not None:
        cache_put(conn, key, result, kw.get("model", CHEAP_MODEL))
    return result


def _gemini_schema(schema: dict) -> dict:
    """Strip JSON-Schema keys Gemini's response_schema rejects (e.g.
    additionalProperties)."""
    clean = copy.deepcopy(schema)

    def _walk(node):
        if isinstance(node, dict):
            node.pop("additionalProperties", None)
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(clean)
    return clean


def generate_json(
    prompt: str,
    *,
    schema: dict,
    system: str | None = None,
    model: str = CHEAP_MODEL,
    max_tokens: int = 1024,
):
    """One structured LLM call. Returns parsed JSON, or None if no
    client/credentials are available or the call fails (callers fall back)."""
    client = get_client()
    if client is None:
        return None
    _STATS["calls"] += 1
    try:
        from google.genai import types
    except Exception as exc:  # noqa: BLE001
        log.warning("google-genai unavailable: %s", exc)
        return None

    default_system = "You are a component in a search pipeline. Output only JSON."
    base = {
        "response_mime_type": "application/json",
        "max_output_tokens": max_tokens,
        "system_instruction": system or default_system,
    }
    try:
        config = types.GenerateContentConfig(response_schema=_gemini_schema(schema), **base)
        resp = client.models.generate_content(model=model, contents=prompt, config=config)
    except Exception as exc:  # noqa: BLE001 - retry without schema; mime type still enforces JSON
        log.debug("schema-constrained call failed (%s); retrying plain JSON", exc)
        try:
            config = types.GenerateContentConfig(**base)
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
        except Exception as exc2:  # noqa: BLE001 - degrade to heuristics, never crash retrieval
            log.warning("LLM call failed (%s); falling back", exc2)
            return None
    try:
        return json.loads(resp.text)
    except (ValueError, AttributeError, TypeError) as exc:
        # blocked/empty response or non-JSON text
        log.warning("LLM returned no usable JSON (%s); falling back", exc)
        return None
