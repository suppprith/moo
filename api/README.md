# moo-api

FastAPI backend for **moo search**, managed with [uv](https://docs.astral.sh/uv/).

## Develop

```bash
uv sync                       # install deps into .venv
uv run fastapi dev app/main.py   # dev server with reload on http://127.0.0.1:8000
uv run ruff check .           # lint
uv run ruff format .          # format
```

- `GET /health` → `{"status": "ok"}` liveness probe.
