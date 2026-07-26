"""Pluggable LLM provider config + dispatch."""

import json

import pytest

from app import llm

_ENV = ["MOO_LLM_PROVIDER", "MOO_LLM_API_KEY", "MOO_LLM_MODEL", "MOO_LLM_BASE_URL",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setattr(llm, "_load_dotenv", lambda: None)
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)
    llm._reset_config()
    yield
    llm._reset_config()


def _cfg():
    llm._reset_config()
    return llm._resolve_config()


def test_no_provider_returns_none():
    assert _cfg() is None
    assert llm.generate_json("p", schema={}) is None


def test_infers_gemini_from_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    c = _cfg()
    assert c["provider"] == "gemini" and c["model"] == "gemini-2.5-flash"


def test_infers_anthropic_and_openai(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert _cfg()["provider"] == "anthropic"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert _cfg()["provider"] == "openai"


def test_explicit_provider_and_model_override(monkeypatch):
    monkeypatch.setenv("MOO_LLM_PROVIDER", "openai")
    monkeypatch.setenv("MOO_LLM_API_KEY", "k")
    monkeypatch.setenv("MOO_LLM_MODEL", "gpt-4o")
    c = _cfg()
    assert c["provider"] == "openai" and c["model"] == "gpt-4o"


def test_ollama_needs_no_key(monkeypatch):
    monkeypatch.setenv("MOO_LLM_PROVIDER", "ollama")
    c = _cfg()
    assert c and c["provider"] == "ollama" and c["base_url"].endswith("11434/v1")


def test_provider_without_key_falls_back(monkeypatch):
    monkeypatch.setenv("MOO_LLM_PROVIDER", "openai")
    assert _cfg() is None


def test_unknown_provider_falls_back(monkeypatch):
    monkeypatch.setenv("MOO_LLM_PROVIDER", "bogus")
    monkeypatch.setenv("MOO_LLM_API_KEY", "k")
    assert _cfg() is None


def test_cheap_model_tracks_config(monkeypatch):
    monkeypatch.setenv("MOO_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MOO_LLM_API_KEY", "k")
    llm._reset_config()
    assert llm.model_name().startswith("claude")
    assert llm.CHEAP_MODEL == llm.model_name()


def test_generate_json_dispatches_by_provider(monkeypatch):
    monkeypatch.setenv("MOO_LLM_PROVIDER", "openai")
    monkeypatch.setenv("MOO_LLM_API_KEY", "k")
    llm._reset_config()
    seen = {}

    def fake(cfg, prompt, schema, system, model, max_tokens):
        seen["model"] = model
        return {"ok": 1}

    monkeypatch.setitem(llm._BACKENDS, "openai", fake)
    assert llm.generate_json("prompt", schema={"type": "object"}) == {"ok": 1}
    assert seen["model"] == "gpt-4o-mini"


def test_backend_exception_degrades_to_none(monkeypatch):
    monkeypatch.setenv("MOO_LLM_PROVIDER", "openai")
    monkeypatch.setenv("MOO_LLM_API_KEY", "k")
    llm._reset_config()

    def boom(*a, **k):
        raise RuntimeError("upstream down")

    monkeypatch.setitem(llm._BACKENDS, "openai", boom)
    assert llm.generate_json("p", schema={}) is None


def test_extract_json_strips_fences_and_prose():
    assert json.loads(llm._extract_json('```json\n{"a": 1}\n```')) == {"a": 1}
    assert json.loads(llm._extract_json('sure: {"a": 2} done')) == {"a": 2}


def test_openai_backend_builds_request(monkeypatch):
    import httpx

    monkeypatch.setenv("MOO_LLM_PROVIDER", "openai")
    monkeypatch.setenv("MOO_LLM_API_KEY", "sk-x")
    llm._reset_config()
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"queries": ["a"]}'}}]}

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, body=json, headers=headers)
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    out = llm.generate_json("q", schema={"type": "object"})
    assert out == {"queries": ["a"]}
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-x"
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_anthropic_backend_builds_request(monkeypatch):
    import httpx

    monkeypatch.setenv("MOO_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MOO_LLM_API_KEY", "sk-ant")
    llm._reset_config()
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"text": '{"edges": []}'}]}

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, headers=headers)
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    out = llm.generate_json("q", schema={"type": "object"})
    assert out == {"edges": []}
    assert captured["url"].endswith("/messages")
    assert captured["headers"]["x-api-key"] == "sk-ant"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
