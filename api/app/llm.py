"""Pluggable LLM provider layer — bring your own key.

Every LLM stage in the pipeline goes through ``generate_json()`` so the provider
is swappable and results are cached in ``llm_cache`` by stage + content hash
(repeat runs never hit the API). The operator brings their own key for whatever
provider they run; nothing is hard-coded to one vendor.

Config (env, also read from ``api/.env``):

- ``MOO_LLM_PROVIDER`` — ``gemini`` | ``openai`` | ``openai-compatible`` |
  ``ollama`` | ``anthropic``. If unset, inferred from whichever key is present
  (``GEMINI_API_KEY``/``GOOGLE_API_KEY`` → gemini, ``ANTHROPIC_API_KEY`` →
  anthropic, ``OPENAI_API_KEY`` → openai).
- ``MOO_LLM_API_KEY`` — the key (or the provider-specific env var above).
- ``MOO_LLM_MODEL`` — model override (per-provider default otherwise).
- ``MOO_LLM_BASE_URL`` — for openai-compatible / local servers (Ollama, vLLM,
  LM Studio, llama.cpp).

One OpenAI-compatible path (httpx) covers OpenAI + most hosted/local servers;
Gemini uses ``google-genai``; Anthropic uses the Messages API (httpx). When no
provider resolves, ``generate_json`` returns ``None`` and callers fall back to
their deterministic heuristic — the pipeline always runs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import sqlite3
import threading

from .env import load_dotenv

log = logging.getLogger("moo.llm")

_DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "openai-compatible": "gpt-4o-mini",
    "ollama": "llama3.1",
    "anthropic": "claude-haiku-4-5-20251001",
}
_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
    "anthropic": "https://api.anthropic.com/v1",
}
_KEYLESS_OK = {"ollama", "openai-compatible"}

_FALLBACK_MODEL = _DEFAULT_MODELS["gemini"]




_config: dict | None = None
_config_checked = False


def _infer_provider() -> str | None:
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("MOO_LLM_API_KEY") and os.environ.get("MOO_LLM_BASE_URL"):
        return "openai-compatible"
    return None


def _resolve_config() -> dict | None:
    """Resolve {provider, api_key, base_url, model} from config, or None when no
    provider is available. Cached for the process."""
    global _config, _config_checked
    if _config_checked:
        return _config
    _config_checked = True
    load_dotenv()

    provider = (os.environ.get("MOO_LLM_PROVIDER") or "").strip().lower() or _infer_provider()
    if provider is None:
        log.info("no LLM provider configured; stages use heuristic fallbacks")
        return None
    if provider not in _DEFAULT_MODELS:
        log.warning("unknown MOO_LLM_PROVIDER %r; using heuristics", provider)
        return None

    key = os.environ.get("MOO_LLM_API_KEY") or {
        "gemini": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
        "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
        "openai": os.environ.get("OPENAI_API_KEY"),
    }.get(provider)
    if not key and provider not in _KEYLESS_OK:
        log.info("provider %s selected but no API key found; using heuristics", provider)
        return None

    _config = {
        "provider": provider,
        "api_key": key,
        "base_url": os.environ.get("MOO_LLM_BASE_URL") or _DEFAULT_BASE_URLS.get(provider),
        "model": os.environ.get("MOO_LLM_MODEL") or _DEFAULT_MODELS[provider],
    }
    return _config


def _reset_config() -> None:
    """Re-read config on next use (tests / after env changes)."""
    global _config, _config_checked
    _config, _config_checked = None, False


def model_name() -> str:
    cfg = _resolve_config()
    return cfg["model"] if cfg else _FALLBACK_MODEL


def get_client():
    """Truthy when a provider is configured (back-compat helper)."""
    return _resolve_config()


def __getattr__(name: str):
    if name == "CHEAP_MODEL":
        return model_name()
    raise AttributeError(name)


_STAT_FIELDS = ("calls", "attempts", "cache_hits", "cache_misses", "prompt_chars")
_local = threading.local()


def _stats() -> dict:
    """Counters are thread-local: a research run resets and reads them on its own
    worker thread while request threads meter themselves, and neither clobbers
    the other's totals."""
    stats = getattr(_local, "stats", None)
    if stats is None:
        stats = dict.fromkeys(_STAT_FIELDS, 0)
        _local.stats = stats
    return stats


def reset_stats() -> None:
    _local.stats = dict.fromkeys(_STAT_FIELDS, 0)


def get_stats() -> dict:
    """`calls` are provider requests actually issued; `attempts` counts every
    stage that wanted one, so a keyless run still measures its model demand.
    `prompt_chars` is the input those attempts would have sent."""
    return dict(_stats())


def attempts() -> int:
    return _stats()["attempts"]


def cache_key(stage: str, normalized_input: str) -> str:
    digest = hashlib.sha256(normalized_input.encode("utf-8")).hexdigest()
    return f"{stage}:{digest}"


def cache_get(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM llm_cache WHERE key = ?", (key,)).fetchone()
    stats = _stats()
    if row:
        stats["cache_hits"] += 1
        return json.loads(row[0])
    stats["cache_misses"] += 1
    return None


def cache_put(conn: sqlite3.Connection, key: str, value, model: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO llm_cache (key, value, model) VALUES (?, ?, ?)",
        (key, json.dumps(value, ensure_ascii=False), model),
    )
    conn.commit()


def cached_json(conn: sqlite3.Connection, stage: str, key_input: str, prompt: str, *, schema: dict, **kw):
    """cache_get -> generate_json -> cache_put in one call, keyed by content, so
    every LLM stage is cached. A repeated identical run is a cache hit and makes
    zero API calls. Cache keys are provider-agnostic (content only); the model is
    recorded in the row. Non-results (heuristic fallback) are not cached."""
    key = cache_key(stage, key_input)
    hit = cache_get(conn, key)
    if hit is not None:
        return hit
    result = generate_json(prompt, schema=schema, **kw)
    if result is not None:
        cache_put(conn, key, result, kw.get("model") or model_name())
    return result


def _gemini_schema(schema: dict) -> dict:
    """Strip JSON-Schema keys Gemini's response_schema rejects."""
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


def _extract_json(text: str) -> str:
    """Best-effort: pull the JSON object out of a model response (strip code
    fences / surrounding prose)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    i, j = text.find("{"), text.rfind("}")
    return text[i : j + 1] if i != -1 and j != -1 else text


_clients: dict = {}


def _gemini_json(cfg, prompt, schema, system, model, max_tokens):
    from google import genai
    from google.genai import types

    client = _clients.get(("gemini", cfg["api_key"]))
    if client is None:
        client = genai.Client(api_key=cfg["api_key"])
        _clients[("gemini", cfg["api_key"])] = client
    base = {
        "response_mime_type": "application/json",
        "max_output_tokens": max_tokens,
        "system_instruction": system,
    }
    try:
        config = types.GenerateContentConfig(response_schema=_gemini_schema(schema), **base)
        resp = client.models.generate_content(model=model, contents=prompt, config=config)
    except Exception:  # noqa: BLE001
        config = types.GenerateContentConfig(**base)
        resp = client.models.generate_content(model=model, contents=prompt, config=config)
    return json.loads(resp.text)


def _openai_json(cfg, prompt, schema, system, model, max_tokens):
    import httpx

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system + " Respond with a single JSON object."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=60)
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        body.pop("response_format", None)
        resp = httpx.post(url, json=body, headers=headers, timeout=60)
        resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(_extract_json(content))


def _anthropic_json(cfg, prompt, schema, system, model, max_tokens):
    import httpx

    url = cfg["base_url"].rstrip("/") + "/messages"
    headers = {
        "x-api-key": cfg["api_key"] or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system + " Respond with only a single JSON object, no prose.",
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = httpx.post(url, json=body, headers=headers, timeout=60)
    resp.raise_for_status()
    content = resp.json()["content"][0]["text"]
    return json.loads(_extract_json(content))


_BACKENDS = {
    "gemini": _gemini_json,
    "openai": _openai_json,
    "openai-compatible": _openai_json,
    "ollama": _openai_json,
    "anthropic": _anthropic_json,
}


def generate_json(
    prompt: str,
    *,
    schema: dict,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 1024,
):
    """One structured LLM call via the configured provider. Returns parsed JSON,
    or ``None`` when no provider is configured or the call fails (callers fall
    back to heuristics — an LLM failure never breaks retrieval)."""
    system = system or "You are a component in a search pipeline. Output only JSON."
    stats = _stats()
    stats["attempts"] += 1
    stats["prompt_chars"] += len(prompt) + len(system)
    cfg = _resolve_config()
    if cfg is None:
        return None
    stats["calls"] += 1
    backend = _BACKENDS[cfg["provider"]]
    try:
        return backend(cfg, prompt, schema, system, model or cfg["model"], max_tokens)
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM call failed (%s); falling back", exc)
        return None
