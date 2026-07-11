"""SSE streaming endpoint + helpers (SUP-107)."""

import json

from fastapi.testclient import TestClient

from app import main
from app.streaming import sse_event


def parse_sse(text: str):
    """Parse an SSE body into a list of (event, data-dict)."""
    events = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        events.append((event, data))
    return events


def test_sse_event_format():
    frame = sse_event("progress", {"stage": "searching"})
    assert frame == 'event: progress\ndata: {"stage": "searching"}\n\n'


class _DummyConn:
    def close(self):
        pass


def test_stream_emits_progress_sources_done(monkeypatch):
    monkeypatch.setattr(main, "get_connection_for_search", lambda: _DummyConn())
    monkeypatch.setattr(main, "search", lambda *a, **k: {
        "mode": "raw", "intent": "comparison",
        "sources": [{"id": "chk_1", "url": "u1"}, {"id": "chk_2", "url": "u2"}],
        "claims": [],
        "meta": {"contract_version": "1.1"},
    })
    client = TestClient(main.app)
    r = client.get("/search/stream", params={"q": "postgres vs mysql"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert kinds == ["progress", "source", "source", "done"]
    assert events[1][1]["id"] == "chk_1"
    assert events[-1][1]["meta"]["contract_version"] == "1.1"


def test_stream_reports_error_in_band(monkeypatch):
    monkeypatch.setattr(main, "get_connection_for_search", lambda: _DummyConn())

    def boom(*a, **k):
        raise ValueError("unknown fields")

    monkeypatch.setattr(main, "search", boom)
    client = TestClient(main.app)
    r = client.get("/search/stream", params={"q": "x"})
    assert r.status_code == 200  # status already committed; error is in-band
    events = parse_sse(r.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["error"]["code"] == "invalid_request"
