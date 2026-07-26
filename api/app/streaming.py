"""Server-Sent Events helpers + event schema.

Long endpoints stream newline-delimited SSE frames::

    event: <type>\\n
    data: <json>\\n\\n

Event types:

- ``progress`` — ``{stage, ...}`` a work-in-progress signal (no payload rows yet)
- ``source``   — one agent-shaped source row, emitted progressively
- ``claim``    — one agent-shaped claim row
- ``error``    — ``{error: {...envelope}}`` terminal; the stream then closes
- ``done``     — ``{meta, ...}`` terminal success

Consumers treat ``error`` and ``done`` as end-of-stream. Because SSE commits the
200 status + headers on the first byte, a failure mid-stream is reported as an
``error`` event rather than an HTTP status.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from starlette.responses import StreamingResponse


def sse_event(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def sse_response(frames: Iterable[str]) -> StreamingResponse:
    """Wrap a frame iterator as a streaming ``text/event-stream`` response.
    ``X-Accel-Buffering: no`` disables proxy buffering so frames flush promptly."""
    return StreamingResponse(
        frames,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
