"""Fetch / drill-down resolution for agent citations.

An agent gets opaque handles (``chk_``/``clm_``/``doc_``) in an agent-shaped
search response; these resolvers turn a handle back into the full underlying row
so the agent can move from a citation to the actual evidence. The same functions
back the HTTP fetch endpoints and (later) the MCP fetch tools.

Every resolver returns ``None`` when the row does not exist, so the caller can
map that to a 404. Deep links come from ``chunk.url_anchor`` (document URL +
``#heading`` — a GitHub comment permalink, docs-section anchor, etc.).
"""

from __future__ import annotations

import sqlite3

from . import ids

_CONTEXT_PREVIEW = 240


def _preview(text: str | None, n: int = _CONTEXT_PREVIEW) -> str | None:
    if not text:
        return None
    text = text.strip()
    return text if len(text) <= n else text[:n].rstrip() + "…"


def fetch_document(conn: sqlite3.Connection, doc_id: int) -> dict | None:
    """Full document behind a source: cleaned text + metadata + chunk handles."""
    row = conn.execute(
        """SELECT id, source_type, url, title, author, author_role, published_at,
                  updated_at, fetched_at, popularity, trust_score, raw_text
           FROM document WHERE id = ?""",
        (doc_id,),
    ).fetchone()
    if row is None:
        return None
    chunks = conn.execute(
        "SELECT id, heading, url_anchor FROM chunk WHERE document_id = ? ORDER BY ordinal",
        (doc_id,),
    ).fetchall()
    return {
        "id": ids.encode(ids.DOCUMENT, row["id"]),
        "url": row["url"],
        "title": row["title"],
        "source_type": row["source_type"],
        "trust_score": row["trust_score"],
        "author": row["author"],
        "author_role": row["author_role"],
        "published_at": row["published_at"],
        "updated_at": row["updated_at"],
        "fetched_at": row["fetched_at"],
        "popularity": row["popularity"],
        "text": row["raw_text"],
        "chunks": [
            {"id": ids.encode(ids.CHUNK, c["id"]), "heading": c["heading"], "url": c["url_anchor"]}
            for c in chunks
        ],
    }


def fetch_chunk(conn: sqlite3.Connection, chunk_id: int) -> dict | None:
    """A chunk with its full text, its document handle, and the surrounding
    chunks (prev/next by ordinal) as handles + previews for further drill-down."""
    row = conn.execute(
        """SELECT ch.id, ch.document_id, ch.ordinal, ch.heading, ch.text, ch.url_anchor,
                  ch.canonical_chunk_id,
                  d.source_type, d.title, d.trust_score, d.author_role, d.published_at
           FROM chunk ch JOIN document d ON d.id = ch.document_id
           WHERE ch.id = ?""",
        (chunk_id,),
    ).fetchone()
    if row is None:
        return None

    def neighbour(delta: int) -> dict | None:
        n = conn.execute(
            "SELECT id, heading, text FROM chunk WHERE document_id = ? AND ordinal = ?",
            (row["document_id"], row["ordinal"] + delta),
        ).fetchone()
        if n is None:
            return None
        return {
            "id": ids.encode(ids.CHUNK, n["id"]),
            "heading": n["heading"],
            "preview": _preview(n["text"]),
        }

    out = {
        "id": ids.encode(ids.CHUNK, row["id"]),
        "document": ids.encode(ids.DOCUMENT, row["document_id"]),
        "url": row["url_anchor"],
        "title": row["title"],
        "source_type": row["source_type"],
        "trust_score": row["trust_score"],
        "author_role": row["author_role"],
        "published_at": row["published_at"],
        "heading": row["heading"],
        "ordinal": row["ordinal"],
        "text": row["text"],
        "context": {"prev": neighbour(-1), "next": neighbour(+1)},
    }
    if row["canonical_chunk_id"] is not None:
        out["canonical"] = ids.encode(ids.CHUNK, row["canonical_chunk_id"])
    return out


def fetch_claim(conn: sqlite3.Connection, claim_id: int) -> dict | None:
    """A claim with confidence + its full evidence set (each edge carrying the
    backing chunk handle, deep link, and an excerpt) + extraction provenance."""
    claim = conn.execute(
        "SELECT id, text, confidence, disputed FROM claim WHERE id = ?", (claim_id,)
    ).fetchone()
    if claim is None:
        return None
    edges = conn.execute(
        """SELECT e.relation, e.strength, e.rationale, e.chunk_id,
                  ch.text, ch.url_anchor, d.source_type, d.trust_score
           FROM evidence e
           JOIN chunk ch ON ch.id = e.chunk_id
           JOIN document d ON d.id = ch.document_id
           WHERE e.claim_id = ?
           ORDER BY (e.relation = 'contradicts') DESC, e.strength DESC""",
        (claim_id,),
    ).fetchall()
    extracted = conn.execute(
        "SELECT chunk_id FROM claim_chunk WHERE claim_id = ? ORDER BY chunk_id", (claim_id,)
    ).fetchall()
    entities = conn.execute(
        "SELECT entity_id FROM claim_entity WHERE claim_id = ? ORDER BY entity_id", (claim_id,)
    ).fetchall()
    return {
        "id": ids.encode(ids.CLAIM, claim["id"]),
        "text": claim["text"],
        "confidence": claim["confidence"],
        "disputed": bool(claim["disputed"]),
        "evidence": [
            {
                "relation": e["relation"],
                "source": ids.encode(ids.CHUNK, e["chunk_id"]),
                "strength": e["strength"],
                "rationale": e["rationale"],
                "url": e["url_anchor"],
                "source_type": e["source_type"],
                "trust_score": e["trust_score"],
                "excerpt": _preview(e["text"]),
            }
            for e in edges
        ],
        "extracted_from": [ids.encode(ids.CHUNK, r["chunk_id"]) for r in extracted],
        "entities": [ids.encode(ids.ENTITY, r["entity_id"]) for r in entities],
    }


_RESOLVERS = {ids.DOCUMENT: fetch_document, ids.CHUNK: fetch_chunk, ids.CLAIM: fetch_claim}


def fetch_handle(conn: sqlite3.Connection, handle: str) -> dict | None:
    """Decode `handle` and dispatch to the matching resolver. Raises ``ValueError``
    on a malformed handle or a kind with no fetch resolver (e.g. an entity)."""
    kind, rowid = ids.decode(handle)
    resolver = _RESOLVERS.get(kind)
    if resolver is None:
        raise ValueError(f"no fetch resolver for {kind!r} handle")
    return resolver(conn, rowid)
