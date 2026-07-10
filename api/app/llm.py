"""Pluggable LLM provider layer (SUP-82; groundwork for SUP-104).

Every LLM stage in the pipeline goes through ``generate_json()`` so the
provider is swappable (Claude API now; Ollama later per SUP-104) and results
are cached in the ``llm_cache`` table by stage + normalized-input hash —
repeat queries never hit the API.

Per the project stack decision, cheap pipeline stages (expansion, claims,
rerank) default to Claude Haiku. Credentials resolve via the SDK's normal
chain (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / an `ant auth login`
profile); when none are available, callers fall back to their heuristic path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3

log = logging.getLogger("moo.llm")

CHEAP_MODEL = "claude-haiku-4-5"  # expansion/claims/rerank per stack decision

_client = None
_client_checked = False


def get_client():
    """Return an anthropic.Anthropic client, or None if no credentials resolve."""
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    try:
        import anthropic

        client = anthropic.Anthropic()
        # the SDK resolves env vars / auth profiles lazily; probe cheaply
        if client.api_key or client.auth_token:
            _client = client
        else:
            log.info("no Anthropic credentials found; LLM stages use heuristic fallbacks")
    except Exception as exc:  # noqa: BLE001 - LLM absence must never break retrieval
        log.warning("anthropic client unavailable: %s", exc)
    return _client


def cache_key(stage: str, normalized_input: str) -> str:
    digest = hashlib.sha256(normalized_input.encode("utf-8")).hexdigest()
    return f"{stage}:{digest}"


def cache_get(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM llm_cache WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def cache_put(conn: sqlite3.Connection, key: str, value, model: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO llm_cache (key, value, model) VALUES (?, ?, ?)",
        (key, json.dumps(value, ensure_ascii=False), model),
    )
    conn.commit()


def generate_json(
    prompt: str,
    *,
    schema: dict,
    system: str | None = None,
    model: str = CHEAP_MODEL,
    max_tokens: int = 1024,
):
    """One structured LLM call. Returns the parsed JSON, or None if no
    client/credentials are available or the call fails (callers fall back)."""
    client = get_client()
    if client is None:
        return None
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system or "You are a component in a search pipeline. Output only JSON.",
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            log.warning("LLM refused; falling back")
            return None
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001 - degrade to heuristics, never crash retrieval
        log.warning("LLM call failed (%s); falling back", exc)
        return None
