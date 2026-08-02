import asyncio

import httpx
import pytest

from moo import AsyncMoo, Moo, MooError
from moo._transport import iter_sse

FRAMES = (
    b"event: progress\ndata: {\"stage\": \"searching\"}\n\n"
    b"event: source\ndata: {\"id\": \"chk_1\"}\n\n"
    b"event: done\ndata: {\"mode\": \"raw\"}\n\n"
)


def stream_handler(body: bytes, status: int = 200):
    def handler(request):
        return httpx.Response(status, content=body,
                              headers={"content-type": "text/event-stream"})

    return handler


def test_iter_sse_parses_events_and_multiline_data():
    lines = ["event: plan", "data: {\"a\": 1}", "", ": comment", "event: done", "data: {\"b\":",
             "data: 2}", ""]
    assert list(iter_sse(lines)) == [("plan", {"a": 1}), ("done", {"b": 2})]


def test_search_stream_yields_events():
    client = Moo(base_url="https://moo.test",
                 http_client=httpx.Client(transport=httpx.MockTransport(stream_handler(FRAMES))))
    events = list(client.search_stream("wal mode", mode="raw"))
    assert [name for name, _ in events] == ["progress", "source", "done"]
    assert events[1][1] == {"id": "chk_1"}


def test_error_event_raises_typed_error():
    body = (b"event: progress\ndata: {\"stage\": \"planning\"}\n\n"
            b"event: error\ndata: {\"error\": {\"code\": \"internal\", \"message\": "
            b"\"research failed\", \"retryable\": true, \"request_id\": \"rid\"}}\n\n")
    client = Moo(base_url="https://moo.test",
                 http_client=httpx.Client(transport=httpx.MockTransport(stream_handler(body))))
    stream = client.research_stream("why")
    assert next(stream)[0] == "progress"
    with pytest.raises(MooError) as exc:
        next(stream)
    assert exc.value.code == "internal"
    assert exc.value.request_id == "rid"


def test_stream_failure_before_first_byte_raises():
    body = b"{\"error\": {\"code\": \"unauthorized\", \"message\": \"no key\", " \
           b"\"retryable\": false, \"request_id\": \"r\"}}"
    client = Moo(base_url="https://moo.test",
                 http_client=httpx.Client(
                     transport=httpx.MockTransport(stream_handler(body, status=401))))
    with pytest.raises(MooError) as exc:
        list(client.search_stream("q"))
    assert exc.value.code == "unauthorized"


def test_async_stream_yields_events():
    async def go():
        async with AsyncMoo(
            base_url="https://moo.test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(stream_handler(FRAMES))),
        ) as client:
            return [name async for name, _ in client.search_stream("q")]

    assert asyncio.run(go()) == ["progress", "source", "done"]
