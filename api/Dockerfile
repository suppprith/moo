# moo API image.
#
# Two things dominate a first request: installing the dependency tree and
# loading the embedding model. Both are moved into the build, so a container
# starts with the model already on disk and warms it on boot rather than on the
# first search. The image is large (torch and sentence-transformers), which is
# the cost of running embeddings locally instead of paying an embedding API.

FROM python:3.11-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    HF_HOME=/opt/models \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies first: this layer is reused until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Bake the embedding weights so a cold container never downloads them.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('BAAI/bge-small-en-v1.5', device='cpu')"

COPY app ./app
COPY migrations ./migrations
COPY README.md ./
RUN uv sync --frozen --no-dev

# The store lives on a mounted volume; the image itself stays stateless.
ENV MOO_DB_PATH=/data/moo.sqlite \
    MOO_PRELOAD=1 \
    MOO_CORS_ORIGINS="" \
    PORT=8000
RUN mkdir -p /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,os; \
    urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/health\")"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
