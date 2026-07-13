"""URL-discovery providers (app.live.providers, SUP-130). No network."""

import httpx
import pytest

from app.live.providers import (
    BraveProvider,
    SearxngProvider,
    get_stats,
    reset_stats,
    resolve_provider,
)


class StubResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


class StubClient:
    def __init__(self, payload, status=200):
        self._resp = StubResponse(payload, status)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        return self._resp


# -- searxng ----------------------------------------------------------------------

SEARX_PAYLOAD = {
    "results": [
        {"url": "https://docs.python.org/3/library/gc.html", "title": "gc", "content": "docs"},
        {"url": "https://stackoverflow.com/q/1", "title": "SO", "content": "answer"},
        {"url": "https://docs.python.org/3/library/gc.html", "title": "dup", "content": ""},
        {"url": "ftp://not-http.example.com/x", "title": "bad scheme", "content": ""},
    ]
}


def test_searxng_parses_and_dedupes():
    p = SearxngProvider("http://localhost:8888/", client=StubClient(SEARX_PAYLOAD))
    got = p.discover("python gc", count=10)
    assert [c.url for c in got] == [
        "https://docs.python.org/3/library/gc.html",
        "https://stackoverflow.com/q/1",
    ]
    assert got[0].title == "gc" and got[0].rank == 0


def test_searxng_error_returns_empty():
    p = SearxngProvider("http://localhost:8888", client=StubClient({}, status=500))
    assert p.discover("anything") == []


def test_searxng_respects_count():
    p = SearxngProvider("http://x", client=StubClient(SEARX_PAYLOAD))
    assert len(p.discover("q", count=1)) == 1


# -- brave ------------------------------------------------------------------------

BRAVE_PAYLOAD = {
    "web": {
        "results": [
            {"url": "https://redis.io/docs/persistence/", "title": "Persistence",
             "description": "RDB and AOF"},
            {"url": "https://news.ycombinator.com/item?id=1", "title": "HN",
             "description": "thread"},
        ]
    }
}


def test_brave_parses():
    client = StubClient(BRAVE_PAYLOAD)
    p = BraveProvider("key123", client=client)
    got = p.discover("redis persistence")
    assert [c.url for c in got] == [
        "https://redis.io/docs/persistence/",
        "https://news.ycombinator.com/item?id=1",
    ]
    # key travels in the header, not the URL
    _, kw = client.calls[0]
    assert kw["headers"]["X-Subscription-Token"] == "key123"


def test_brave_error_returns_empty():
    p = BraveProvider("key", client=StubClient({}, status=429))
    assert p.discover("q") == []


def test_call_stats_metered():
    reset_stats()
    SearxngProvider("http://x", client=StubClient(SEARX_PAYLOAD)).discover("q")
    BraveProvider("k", client=StubClient({}, status=500)).discover("q")
    stats = get_stats()
    assert stats["calls"] == 2 and stats["errors"] == 1


# -- resolve_provider from env -------------------------------------------------------

CFG_VARS = [
    "MOO_SEARCH_PROVIDER", "MOO_SEARXNG_URL", "MOO_SEARCH_URL",
    "MOO_BRAVE_API_KEY", "MOO_SEARCH_API_KEY",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in CFG_VARS:
        monkeypatch.delenv(var, raising=False)


def test_resolve_none_when_unconfigured():
    assert resolve_provider() is None


def test_resolve_infers_brave_from_key(monkeypatch):
    monkeypatch.setenv("MOO_BRAVE_API_KEY", "k")
    assert isinstance(resolve_provider(), BraveProvider)


def test_resolve_infers_searxng_from_url(monkeypatch):
    monkeypatch.setenv("MOO_SEARXNG_URL", "http://localhost:8888")
    p = resolve_provider()
    assert isinstance(p, SearxngProvider) and p.base_url == "http://localhost:8888"


def test_explicit_provider_wins(monkeypatch):
    monkeypatch.setenv("MOO_BRAVE_API_KEY", "k")
    monkeypatch.setenv("MOO_SEARXNG_URL", "http://x")
    monkeypatch.setenv("MOO_SEARCH_PROVIDER", "searxng")
    assert isinstance(resolve_provider(), SearxngProvider)


def test_explicit_off_disables(monkeypatch):
    monkeypatch.setenv("MOO_BRAVE_API_KEY", "k")
    monkeypatch.setenv("MOO_SEARCH_PROVIDER", "off")
    assert resolve_provider() is None


def test_explicit_provider_missing_credential_is_none(monkeypatch):
    monkeypatch.setenv("MOO_SEARCH_PROVIDER", "brave")  # no key set
    assert resolve_provider() is None
