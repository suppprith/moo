"""Claim extraction from retrieved chunks (SUP-83) — the heart of the product.

`extract_claims(conn, query)` retrieves the top chunks, pulls out declarative
claims relevant to the query, and persists each as a `claim` row linked to its
source chunks (`claim_chunk`). Near-identical claims are merged via embedding
similarity over claim text, and a hallucination guard drops any claim whose text
isn't grounded in its source chunk.

Primary path is a structured Gemini call (`app.llm`); a deterministic
sentence-extraction fallback keeps the pipeline runnable without credentials —
lower quality, but grounded by construction (claims are literal chunk sentences).

CLI:  ``uv run python -m app.evidence.claims "Postgres vs MySQL for a new app"``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3

import numpy as np

from .. import llm
from ..embed import embed_texts
from ..retrieve import retrieve
from ..understand import BUILTIN_ALIASES

log = logging.getLogger("moo.claims")

MAX_CLAIMS = 12
MERGE_COSINE = 0.88     # claims closer than this are the same claim
GROUND_COSINE = 0.30    # a claim must be at least this similar to its source chunk

_FENCE = re.compile(r"```.*?```", re.S)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"'(])")
_ENTITY_WORDS = set(BUILTIN_ALIASES)
_ASSERTION_CUE = re.compile(
    r"\b(better|worse|faster|slower|more|less|should|must|because|due to|causes?|"
    r"prefer|avoid|recommend|instead|unlike|whereas|is the|are the|handles?|"
    r"outperforms?|scales?|supports?|lacks?|cannot|does not|doesn't)\b",
    re.I,
)

_CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "chunk_id": {"type": "integer"},
                    "quote": {"type": "string"},
                },
                "required": ["text", "chunk_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

_PROMPT = """You extract declarative claims from developer sources about databases.
From the chunks below, extract the standalone factual/opinion claims relevant to the
query. Each claim must be a single self-contained sentence (resolve pronouns), quote or
closely paraphrase its chunk, and cite the chunk_id it came from. Skip questions, code,
and boilerplate. Return at most {max} claims.

Query: {query}

Chunks:
{chunks}"""


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def _sentences(text: str) -> list[str]:
    prose = _FENCE.sub(" ", text)             # drop fenced code blocks
    prose = re.sub(r"`[^`]*`", " ", prose)    # drop inline code spans
    prose = re.sub(r"[#>*_|]+", " ", prose)   # strip markdown marks
    prose = re.sub(r"\s+", " ", prose).strip()  # flatten newlines/whitespace
    out = []
    for raw in _SENT_SPLIT.split(prose):
        s = raw.strip()
        n = len(s.split())
        # readable prose sentence: right length, not a question, mostly words
        if 6 <= n <= 40 and not s.endswith("?") and len(re.findall(r"[a-zA-Z]", s)) >= 0.6 * len(s):
            out.append(s)
    return out


def _candidate_score(sentence: str) -> int:
    words = set(_normalize(sentence).split())
    return (2 if _ASSERTION_CUE.search(sentence) else 0) + (1 if words & _ENTITY_WORDS else 0)


def _heuristic_extract(chunks: list[tuple[int, str]], max_claims: int) -> list[dict]:
    scored: list[tuple[int, dict]] = []
    for chunk_id, text in chunks:
        for sent in _sentences(text):
            score = _candidate_score(sent)
            if score > 0:
                scored.append((score, {"text": sent, "chunk_id": chunk_id}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[: max_claims * 2]]  # trimmed after clustering


def _llm_extract(
    conn: sqlite3.Connection, query: str, chunks: list[tuple[int, str]], max_claims: int
) -> list[dict] | None:
    joined = "\n\n".join(f"[chunk {cid}] {text[:700]}" for cid, text in chunks)
    key_input = _normalize(query) + ":" + ",".join(str(cid) for cid, _ in sorted(chunks))
    payload = llm.cached_json(
        conn, "claims", key_input,
        _PROMPT.format(max=max_claims, query=query, chunks=joined),
        schema=_CLAIM_SCHEMA,
        max_tokens=1500,
    )
    if not payload or not isinstance(payload.get("claims"), list):
        return None
    valid_ids = {cid for cid, _ in chunks}
    return [
        {"text": c["text"].strip(), "chunk_id": c["chunk_id"]}
        for c in payload["claims"]
        if c.get("text") and c.get("chunk_id") in valid_ids
    ]


def _chunk_embeddings(conn: sqlite3.Connection, chunk_ids: set[int]) -> dict[int, np.ndarray]:
    if not chunk_ids:
        return {}
    qmarks = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"SELECT id, embedding FROM chunk WHERE id IN ({qmarks}) AND embedding IS NOT NULL",
        list(chunk_ids),
    ).fetchall()
    return {r["id"]: np.frombuffer(r["embedding"], dtype=np.float32) for r in rows}


def _persist(conn: sqlite3.Connection, candidates: list[dict], model: str) -> list[dict]:
    if not candidates:
        return []
    vecs = embed_texts([c["text"] for c in candidates])
    chunk_vecs = _chunk_embeddings(conn, {c["chunk_id"] for c in candidates})

    # hallucination guard: claim must be grounded in its source chunk
    grounded: list[tuple[dict, np.ndarray]] = []
    for cand, vec in zip(candidates, vecs, strict=True):
        cvec = chunk_vecs.get(cand["chunk_id"])
        if cvec is None or float(vec @ cvec) >= GROUND_COSINE:
            grounded.append((cand, vec))

    # greedy near-duplicate clustering over claim text
    clusters: list[dict] = []  # {text, vec, chunk_ids}
    for cand, vec in grounded:
        merged = False
        for cl in clusters:
            if float(vec @ cl["vec"]) >= MERGE_COSINE:
                cl["chunk_ids"].add(cand["chunk_id"])
                if len(cand["text"]) < len(cl["text"]):
                    cl["text"] = cand["text"]  # keep the tightest phrasing
                merged = True
                break
        if not merged:
            clusters.append({"text": cand["text"], "vec": vec, "chunk_ids": {cand["chunk_id"]}})

    results = []
    for cl in clusters[:MAX_CLAIMS]:
        key = hashlib.sha256(_normalize(cl["text"]).encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO claim (text, normalized_key) VALUES (?, ?)",
            (cl["text"], key),
        )
        claim_id = conn.execute(
            "SELECT id FROM claim WHERE normalized_key = ?", (key,)
        ).fetchone()[0]
        for chunk_id in cl["chunk_ids"]:
            conn.execute(
                "INSERT OR IGNORE INTO claim_chunk (claim_id, chunk_id) VALUES (?, ?)",
                (claim_id, chunk_id),
            )
        results.append({"id": claim_id, "text": cl["text"], "chunk_ids": sorted(cl["chunk_ids"])})
    conn.commit()
    log.info("stored %d claims (model=%s)", len(results), model)
    return results


def extract_claims(
    conn: sqlite3.Connection, query: str, *, k: int = 8, use_llm: bool = True
) -> list[dict]:
    hits = retrieve(conn, query, k=k)
    chunks = [(h.chunk_id, h.text) for h in hits]
    raw = _llm_extract(conn, query, chunks, MAX_CLAIMS) if use_llm else None
    model = llm.CHEAP_MODEL if raw else "heuristic"
    if not raw:
        raw = _heuristic_extract(chunks, MAX_CLAIMS)
    return _persist(conn, raw, model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.evidence.claims", description="Extract claims")
    parser.add_argument("query")
    parser.add_argument("-k", type=int, default=8)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from ..index.vector import connect

    conn = connect()
    claims = extract_claims(conn, args.query, k=args.k, use_llm=not args.no_llm)
    print(json.dumps(claims, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
