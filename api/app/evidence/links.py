"""Evidence linking: supports / contradicts / explains.

For each claim, retrieve related chunks and classify the (claim, chunk)
relationship as a typed ``evidence`` edge with a strength. Contradiction
detection is the differentiator — contradicting evidence is stored, never
dropped, so the UI can surface disagreement instead of averaging it away.

Primary path is a batched Gemini call per claim; a heuristic fallback uses
embedding similarity plus polarity opposition (:mod:`.opposition`) so the edge
graph exists without credentials — coarser, but a contradiction edge means the
source actually says the opposite, not merely that it contains the word "not".

The keyless path compares **assertion to assertion**: a candidate chunk is
tested through the claims already extracted from it, falling back to its
sentences. Comparing a claim against 600 characters of prose was the old bug —
somewhere in that much text there is always a negation.

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
from . import opposition
from .claims import _sentences

log = logging.getLogger("moo.links")

PER_CLAIM = 6
STRONG_SUPPORT_SIM = 0.55  # this close, the chunk is restating the claim
EXPLAIN_SIM = 0.40         # this close and giving a reason, it explains it
SUPPORT_SIM = 0.45         # weaker restatement, once explanation is ruled out
MAX_ASSERTIONS = 8

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


def _assertions(chunk_text: str, claim_texts: list[str] | None = None) -> list[str]:
    """What this chunk actually asserts. Prefer the claims already extracted
    from it — they are single assertions — and fall back to its sentences."""
    if claim_texts:
        return claim_texts[:MAX_ASSERTIONS]
    return _sentences(chunk_text)[:MAX_ASSERTIONS] or [chunk_text]


def _classify_heuristic(
    claim_text: str, chunk_text: str, sim: float, claim_texts: list[str] | None = None,
    *, allow_contradiction: bool = True,
) -> tuple[str, float] | None:
    """Relate one candidate chunk to the claim, without a model.

    Contradiction is checked assertion by assertion: the chunk contradicts the
    claim when something it asserts is the *opposite* of the claim, not when it
    happens to contain a contrast word. ``allow_contradiction=False`` is for
    chunks from the claim's own document — a page that qualifies its own
    statement ("...but not on Windows") is one voice, not a disagreement, and
    counting it as one lets a single source manufacture a dispute."""
    if allow_contradiction:
        for assertion in _assertions(chunk_text, claim_texts):
            opposed = opposition.opposes(claim_text, assertion, sim=sim)
            if opposed:
                return "contradicts", opposed[1]
    if sim >= STRONG_SUPPORT_SIM:
        return "supports", round(sim, 3)
    if _CAUSAL.search(chunk_text) and sim >= EXPLAIN_SIM:
        return "explains", round(0.3 + 0.4 * sim, 3)
    if sim >= SUPPORT_SIM:
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


def _claims_by_chunk(
    conn: sqlite3.Connection, chunk_ids: list[int], *, exclude_claim_id: int | None = None
) -> dict[int, list[str]]:
    """The claims already extracted from each candidate chunk, so opposition is
    judged assertion against assertion. The claim being linked is excluded — a
    claim never contradicts itself through its own source."""
    if not chunk_ids:
        return {}
    qmarks = ",".join("?" * len(chunk_ids))
    params: list = list(chunk_ids)
    sql = (
        f"SELECT cc.chunk_id, c.text FROM claim_chunk cc JOIN claim c ON c.id = cc.claim_id "
        f"WHERE cc.chunk_id IN ({qmarks})"
    )
    if exclude_claim_id is not None:
        sql += " AND c.id != ?"
        params.append(exclude_claim_id)
    out: dict[int, list[str]] = {}
    for row in conn.execute(sql, params):
        out.setdefault(row["chunk_id"], []).append(row["text"])
    return out


def _claim_documents(conn: sqlite3.Connection, claim_id: int) -> set[int]:
    """The documents this claim was extracted from."""
    return {
        r["document_id"]
        for r in conn.execute(
            "SELECT ch.document_id FROM claim_chunk cc JOIN chunk ch ON ch.id = cc.chunk_id "
            "WHERE cc.claim_id = ?",
            (claim_id,),
        )
    }


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
        emb, doc_of = {}, {}
        for r in conn.execute(
            f"SELECT id, document_id, embedding FROM chunk WHERE id IN ({qmarks})", ids
        ):
            doc_of[r["id"]] = r["document_id"]
            if r["embedding"] is not None:
                emb[r["id"]] = np.frombuffer(r["embedding"], dtype=np.float32)
        chunk_claims = _claims_by_chunk(conn, ids, exclude_claim_id=claim_id)
        own_docs = _claim_documents(conn, claim_id)
        edges = []
        for cid, text in candidates:
            if cid not in emb:
                continue
            sim = float(cvec @ emb[cid])
            result = _classify_heuristic(
                claim_text, text, sim, chunk_claims.get(cid),
                allow_contradiction=doc_of.get(cid) not in own_docs,
            )
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


def link_all(conn: sqlite3.Connection, *, use_llm: bool = True,
             rebuild: bool = False) -> dict[str, int]:
    """Link every claim. ``rebuild`` drops the existing edges first — edges are
    written with INSERT OR IGNORE, so without it a store keeps whatever a
    previous (or worse) classifier decided."""
    if rebuild:
        deleted = conn.execute("DELETE FROM evidence").rowcount
        conn.commit()
        log.info("cleared %d existing evidence edge(s)", deleted)
    claims = conn.execute("SELECT id, text FROM claim").fetchall()
    totals: dict[str, int] = {}
    for c in claims:
        for rel, n in link_claim(conn, c["id"], c["text"], use_llm=use_llm).items():
            totals[rel] = totals.get(rel, 0) + n
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.evidence.links", description="Link evidence edges")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--rebuild", action="store_true",
                        help="delete existing evidence edges before relinking")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from ..index.vector import connect

    conn = connect()
    totals = link_all(conn, use_llm=not args.no_llm, rebuild=args.rebuild)
    print(f"evidence edges: {totals or 'none'}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
