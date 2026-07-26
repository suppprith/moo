"""Embedding pipeline.

Batch-encodes chunk text with a small, CPU-friendly sentence-transformers model
(default ``BAAI/bge-small-en-v1.5``, 384-dim) and stores the vector as a raw
float32 BLOB on the chunk, along with the model name + dims. Incremental by
default: only chunks with no embedding — or one from a different model — are
re-encoded.

Run:  ``uv run python -m app.embed``
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time

from .db import get_connection, migrate

log = logging.getLogger("moo.embed")

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

_model_cache: dict[str, object] = {}


def get_model(name: str):
    """Load (and cache) a sentence-transformers model on CPU."""
    if name not in _model_cache:
        from sentence_transformers import SentenceTransformer

        log.info("loading embedding model %s", name)
        _model_cache[name] = SentenceTransformer(name, device="cpu")
    return _model_cache[name]


def embed_texts(texts: list[str], model_name: str = DEFAULT_MODEL, batch_size: int = 32):
    """Return an (n, dims) float32 numpy array of L2-normalized embeddings."""
    model = get_model(model_name)
    return model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).astype("float32")


def embed_corpus(
    conn: sqlite3.Connection,
    *,
    model_name: str = DEFAULT_MODEL,
    batch_size: int = 32,
    reembed: bool = False,
) -> dict[str, int]:
    if reembed:
        rows = conn.execute("SELECT id, text FROM chunk").fetchall()
    else:
        rows = conn.execute(
            "SELECT id, text FROM chunk "
            "WHERE embedding IS NULL OR embedding_model IS NULL OR embedding_model != ?",
            (model_name,),
        ).fetchall()

    if not rows:
        return {"embedded": 0, "dims": 0}

    model = get_model(model_name)
    get_dims = getattr(model, "get_embedding_dimension", None)
    dims = get_dims() if get_dims else model.get_sentence_embedding_dimension()
    t0 = time.perf_counter()
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        vecs = embed_texts([r["text"] for r in batch], model_name, batch_size)
        conn.executemany(
            "UPDATE chunk SET embedding = ?, embedding_model = ?, embedding_dims = ? WHERE id = ?",
            [(v.tobytes(), model_name, dims, r["id"]) for r, v in zip(batch, vecs, strict=True)],
        )
        conn.commit()
        log.info("embedded %d/%d", min(start + batch_size, len(rows)), len(rows))
    elapsed = time.perf_counter() - t0
    log.info("embedded %d chunks in %.1fs (%.1f/s)", len(rows), elapsed, len(rows) / elapsed)
    return {"embedded": len(rows), "dims": dims}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.embed", description="Embed chunks")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--reembed", action="store_true", help="re-embed all chunks")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )
    migrate()
    conn = get_connection()
    stats = embed_corpus(
        conn, model_name=args.model, batch_size=args.batch_size, reembed=args.reembed
    )
    print(f"embedded {stats['embedded']} chunks with {args.model} (dims={stats['dims']})")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
