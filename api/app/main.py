"""moo search API entrypoint.

Run locally with:  uv run fastapi dev app/main.py
"""

from fastapi import FastAPI

app = FastAPI(
    title="moo search API",
    version="0.1.0",
    summary="Evidence-graph search engine",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
