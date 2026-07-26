"""Evidence linking: supports / contradicts / explains.

For each claim, retrieve related chunks and classify the (claim, chunk)
relationship as a typed ``evidence`` edge with a strength. Contradiction
detection is the differentiator — contradicting evidence is stored, never
dropped, so the UI can surface disagreement instead of averaging it away.

Primary path is a batched Gemini call per claim; a heuristic fallback uses
embedding similarity plus contrast/causal lexical cues so the edge graph
exists without credentials (coarser, but contradictions still surface).

Run:  ``uv run python -m app.evidence.links``  (link all claims)
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3

import numpy as np

from .. import llm
from ..embed import embed_texts
from ..retrieve import retrieve

log = logging.getLogger("moo.links")

PER_CLAIM = 6
MIN_RELATED = 0.30
SUPPORT_SIM = 0.55

_CONTRAST = re.compile(
    r"\b(but|however|unlike|whereas|instead|although|though|conversely|"
    r"on the other hand|not|isn't|aren't|won't|doesn't|don't|can't|never|"
    r"worse|slower|avoid|myth|actually)\b",
    re.I,
)
_CAUSAL = re.compile(
    r"\b(because|due to|since|reason|caused by|so that|as a result|explains?)\b", re.I
)

_EDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "integer"},
                    "relation": {"type": "string", "enum": ["supports", "contradicts", "explains"]},
                    "strength": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "required": ["chunk_id", "relation", "strength"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["edges"],
    "additionalProperties": False,
}

_PROMPT = """Classify how each source chunk relates to the CLAIM about databases.
For each chunk pick exactly one relation:
- supports: the chunk asserts or backs the claim
- contradicts: the chunk asserts the opposite, or that the claim is wrong
- explains: the chunk gives the mechanism/reason behind the claim
Give a strength 0..1. Omit chunks that are merely on-topic but neither support,
contradict, nor explain. Never suppress a contradiction.

CLAIM: {claim}

Chunks:
{chunks}"""


def _classify_heuristic(chunk_text: str, sim: float) -> tuple[str, float] | None:
    contrast = bool(_CONTRAST.search(chunk_text))
    causal = bool(_CAUSAL.search(chunk_text))
    if sim >= SUPPORT_SIM and not contrast:
        return "supports", round(sim, 3)
    if contrast and MIN_RELATED <= sim < 0.65:
        return "contradicts", round(0.35 + 0.35 * sim, 3)
    if causal and sim >= 0.40:
        return "explains", round(0.3 + 0.4 * sim, 3)
    if sim >= 0.45:
        return "supports", round(sim, 3)
    return None


def _candidates(conn: sqlite3.Connection, claim_text: str, claim_id: int) -> list[tuple[int, str]]:
    hits = retrieve(conn, claim_text, k=PER_CLAIM)
    pairs = [(h.chunk_id, h.text) for h in hits]
    own = conn.execute(
        "SELECT ch.id, ch.text FROM claim_chunk cc JOIN chunk ch ON ch.id = cc.chunk_id "
        "WHERE cc.claim_id = ?",
        (claim_id,),
    ).fetchall()
    seen = {cid for cid, _ in pairs}
    pairs.extend((r["id"], r["text"]) for r in own if r["id"] not in seen)
    return pairs


def _llm_edges(
    conn: sqlite3.Connection, claim_text: str, candidates: list[tuple[int, str]]
) -> list[dict] | None:
    joined = "\n\n".join(f"[chunk {cid}] {text[:600]}" for cid, text in candidates)
    key_input = claim_text.strip().lower() + ":" + ",".join(str(cid) for cid, _ in sorted(candidates))
    payload = llm.cached_json(
        conn, "links", key_input,
        _PROMPT.format(claim=claim_text, chunks=joined), schema=_EDGE_SCHEMA, max_tokens=1200,
    )
    if not payload or not isinstance(payload.get("edges"), list):
        return None
    valid = {cid for cid, _ in candidates}
    return [e for e in payload["edges"] if e.get("chunk_id") in valid]


def link_claim(conn: sqlite3.Connection, claim_id: int, claim_text: str, *, use_llm: bool) -> dict:
    candidates = _candidates(conn, claim_text, claim_id)
    if not candidates:
        return {}
    edges = _llm_edges(conn, claim_text, candidates) if use_llm else None
    if edges is None:
        cvec = embed_texts([claim_text])[0]
        ids = [cid for cid, _ in candidates]
        qmarks = ",".join("?" * len(ids))
        emb = {
            r["id"]: np.frombuffer(r["embedding"], dtype=np.float32)
            for r in conn.execute(
                f"SELECT id, embedding FROM chunk WHERE id IN ({qmarks}) AND embedding IS NOT NULL",
                ids,
            )
        }
        edges = []
        for cid, text in candidates:
            if cid not in emb:
                continue
            sim = float(cvec @ emb[cid])
            result = _classify_heuristic(text, sim)
            if result:
                rel, strength = result
                edges.append({"chunk_id": cid, "relation": rel, "strength": strength})

    counts: dict[str, int] = {}
    for e in edges:
        conn.execute(
            "INSERT OR IGNORE INTO evidence (claim_id, chunk_id, relation, strength, rationale) "
            "VALUES (?, ?, ?, ?, ?)",
            (claim_id, e["chunk_id"], e["relation"], e.get("strength"), e.get("rationale")),
        )
        counts[e["relation"]] = counts.get(e["relation"], 0) + 1
    conn.commit()
    return counts


def link_all(conn: sqlite3.Connection, *, use_llm: bool = True) -> dict[str, int]:
    claims = conn.execute("SELECT id, text FROM claim").fetchall()
    totals: dict[str, int] = {}
    for c in claims:
        for rel, n in link_claim(conn, c["id"], c["text"], use_llm=use_llm).items():
            totals[rel] = totals.get(rel, 0) + n
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.evidence.links", description="Link evidence edges")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from ..index.vector import connect

    conn = connect()
    totals = link_all(conn, use_llm=not args.no_llm)
    print(f"evidence edges: {totals or 'none'}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
