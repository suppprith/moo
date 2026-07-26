"""Entity & relation extraction — build the knowledge graph nodes/edges.

`build_graph(conn)` populates the `entity` / `entity_alias` / `relation` tables
from the corpus:

1. **Seed** the v1-domain entities (tools + concepts) with their aliases from the
   query-understanding alias map, so the graph always covers the main things a
   user searches for (Postgres/PostgreSQL/pg all resolve to one node).
2. **Extract** additional entities and typed relations per document with a
   batched Gemini call (`app.llm`), grounded in the document text.
3. A heuristic fallback derives `alternative-to` edges from co-occurring tool
   entities inside a comparison-flavored chunk, so the graph is non-empty and
   carries provenance without credentials.

Every entity is deduped against existing rows (canonical name *or* alias) so one
real-world thing = one node. Every relation carries `document_id` provenance
(NULL for the definitional seed edges) and keeps its strongest strength.

CLI:  ``uv run python -m app.graph.entities [--no-llm] [--limit N]``
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3

from .. import llm
from ..understand import BUILTIN_ALIASES

log = logging.getLogger("moo.graph.entities")

ENTITY_TYPES = ("tool", "library", "concept", "algorithm")
RELATION_TYPES = ("alternative-to", "built-with", "used-by", "part-of", "replaces")

SEED_TYPES: dict[str, str] = {
    "PostgreSQL": "tool", "MySQL": "tool", "MariaDB": "tool", "SQLite": "tool",
    "Redis": "tool", "PgBouncer": "tool",
    "JSONB": "concept", "MVCC": "concept", "WAL": "concept", "VACUUM": "concept",
    "Redis RDB": "concept", "Redis AOF": "concept",
}

SEED_RELATIONS: list[tuple[str, str, str]] = [
    ("JSONB", "part-of", "PostgreSQL"),
    ("MVCC", "part-of", "PostgreSQL"),
    ("WAL", "part-of", "PostgreSQL"),
    ("VACUUM", "part-of", "PostgreSQL"),
    ("Redis RDB", "part-of", "Redis"),
    ("Redis AOF", "part-of", "Redis"),
]

_COMPARISON = re.compile(
    r"\bvs\.?\b|\bversus\b|\bcompared? (to|with)\b|\balternative\b|\binstead of\b|"
    r"\brather than\b|\bover\b|\bmigrat(e|ing|ion)\b|\bswitch(ed|ing)? (to|from)\b",
    re.I,
)

_GRAPH_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": list(ENTITY_TYPES)},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                },
                "required": ["name", "type"],
                "additionalProperties": False,
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "object": {"type": "string"},
                    "type": {"type": "string", "enum": list(RELATION_TYPES)},
                    "strength": {"type": "number"},
                },
                "required": ["subject", "object", "type"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entities", "relations"],
    "additionalProperties": False,
}

_PROMPT = """Extract the knowledge graph of a developer document about databases.

Entities are tools, libraries, concepts, or algorithms (e.g. PostgreSQL, JSONB,
MVCC). Give each a type and any aliases used in the text.

Relations connect two entities with exactly one type:
- alternative-to: the two are competing choices for the same job
- replaces: one supersedes/forks the other
- part-of: one is a feature/component of the other
- built-with / used-by: one is built on or operated by the other
Only assert a relation the text actually supports. Use canonical names.

Title: {title}

Text:
{text}"""


def _alias_index(conn: sqlite3.Connection) -> dict[str, int]:
    """Map every known alias/canonical (lowercased) -> entity id, for dedup."""
    idx: dict[str, int] = {}
    for r in conn.execute("SELECT id, canonical_name FROM entity"):
        idx[r["canonical_name"].lower()] = r["id"]
    for r in conn.execute("SELECT entity_id, alias FROM entity_alias"):
        idx[r["alias"].lower()] = r["entity_id"]
    return idx


def upsert_entity(
    conn: sqlite3.Connection,
    idx: dict[str, int],
    name: str,
    etype: str,
    *,
    aliases: list[str] | None = None,
    description: str | None = None,
) -> int | None:
    """Resolve `name` to an existing node (by canonical or alias) or create one.
    Adds any new aliases and backfills a missing description. Returns entity id."""
    name = (name or "").strip()
    if not name:
        return None
    etype = etype if etype in ENTITY_TYPES else "concept"
    all_names = [name] + [a.strip() for a in (aliases or []) if a and a.strip()]

    entity_id = next((idx[n.lower()] for n in all_names if n.lower() in idx), None)
    if entity_id is None:
        cur = conn.execute(
            "INSERT INTO entity (canonical_name, type, description) VALUES (?, ?, ?)",
            (name, etype, description),
        )
        entity_id = cur.lastrowid
        idx[name.lower()] = entity_id
    elif description:
        conn.execute(
            "UPDATE entity SET description = COALESCE(description, ?) WHERE id = ?",
            (description, entity_id),
        )

    for alias in all_names:
        if alias.lower() not in idx:
            idx[alias.lower()] = entity_id
        conn.execute(
            "INSERT OR IGNORE INTO entity_alias (entity_id, alias) VALUES (?, ?)",
            (entity_id, alias),
        )
    return entity_id


def add_relation(
    conn: sqlite3.Connection,
    subject_id: int,
    obj_id: int,
    rtype: str,
    *,
    strength: float | None = None,
    document_id: int | None = None,
) -> bool:
    """Insert a typed edge (self-loops skipped). On conflict keep the strongest
    strength and fill in provenance. Returns True if a new edge was created."""
    if subject_id == obj_id or rtype not in RELATION_TYPES:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO relation "
        "(subject_entity_id, object_entity_id, type, strength, document_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (subject_id, obj_id, rtype, strength, document_id),
    )
    if cur.rowcount == 0:
        conn.execute(
            "UPDATE relation SET strength = MAX(COALESCE(strength, 0), COALESCE(?, 0)), "
            "document_id = COALESCE(document_id, ?) "
            "WHERE subject_entity_id = ? AND object_entity_id = ? AND type = ?",
            (strength, document_id, subject_id, obj_id, rtype),
        )
        return False
    return True


def _canonical_aliases() -> dict[str, list[str]]:
    """Invert BUILTIN_ALIASES: canonical name -> [alias, ...]."""
    out: dict[str, list[str]] = {name: [] for name in SEED_TYPES}
    for alias, canon in BUILTIN_ALIASES.items():
        out.setdefault(canon, [])
        if alias.lower() != canon.lower():
            out[canon].append(alias)
    return out


def seed_domain(conn: sqlite3.Connection, idx: dict[str, int]) -> int:
    """Insert the v1-domain entities + aliases + definitional part-of edges."""
    aliases = _canonical_aliases()
    for name, etype in SEED_TYPES.items():
        upsert_entity(conn, idx, name, etype, aliases=aliases.get(name, []))
    edges = 0
    for subj, rtype, obj in SEED_RELATIONS:
        sid, oid = idx.get(subj.lower()), idx.get(obj.lower())
        if sid and oid:
            edges += add_relation(conn, sid, oid, rtype, strength=0.9)
    conn.commit()
    return edges


def _documents(conn: sqlite3.Connection, limit: int | None) -> list[sqlite3.Row]:
    sql = (
        "SELECT d.id, d.title, "
        "  (SELECT group_concat(ch.text, ' ') FROM chunk ch WHERE ch.document_id = d.id) AS body "
        "FROM document d ORDER BY d.id"
    )
    rows = conn.execute(sql).fetchall()
    rows = [r for r in rows if r["body"]]
    return rows[:limit] if limit else rows


def _llm_graph(title: str | None, text: str) -> dict | None:
    payload = llm.generate_json(
        _PROMPT.format(title=title or "(untitled)", text=text[:3500]),
        schema=_GRAPH_SCHEMA,
        max_tokens=1500,
    )
    if not payload or not isinstance(payload.get("entities"), list):
        return None
    return payload


def _apply_llm(conn: sqlite3.Connection, idx: dict[str, int], doc_id: int, payload: dict) -> int:
    for e in payload.get("entities", []):
        upsert_entity(
            conn, idx, e.get("name", ""), e.get("type", "concept"),
            aliases=e.get("aliases"), description=(e.get("description") or None),
        )
    edges = 0
    for rel in payload.get("relations", []):
        sid = idx.get(str(rel.get("subject", "")).lower())
        oid = idx.get(str(rel.get("object", "")).lower())
        if sid and oid:
            edges += add_relation(
                conn, sid, oid, rel.get("type", ""),
                strength=rel.get("strength"), document_id=doc_id,
            )
    return edges


def _heuristic_relations(conn: sqlite3.Connection, idx: dict[str, int], doc_id: int, body: str) -> int:
    """Co-occurring tool entities in a comparison-flavored document -> alternative-to."""
    if not _COMPARISON.search(body):
        return 0
    tools = {
        idx[name.lower()]
        for name in SEED_TYPES
        if SEED_TYPES[name] == "tool" and re.search(rf"\b{re.escape(name)}\b", body, re.I)
        and name.lower() in idx
    }
    edges = 0
    ordered = sorted(tools)
    for i, sid in enumerate(ordered):
        for oid in ordered[i + 1 :]:
            edges += add_relation(conn, sid, oid, "alternative-to", strength=0.5, document_id=doc_id)
    return edges


def build_graph(
    conn: sqlite3.Connection, *, use_llm: bool = True, limit: int | None = None
) -> dict:
    """Seed the domain entities then extract entities/relations from every
    document. Returns a summary count dict."""
    idx = _alias_index(conn)
    seeded_edges = seed_domain(conn, idx)

    llm_edges = heur_edges = docs_with_llm = 0
    for doc in _documents(conn, limit):
        payload = _llm_graph(doc["title"], doc["body"]) if use_llm else None
        if payload is not None:
            llm_edges += _apply_llm(conn, idx, doc["id"], payload)
            docs_with_llm += 1
        else:
            heur_edges += _heuristic_relations(conn, idx, doc["id"], doc["body"])
    conn.commit()

    entities = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    relations = conn.execute("SELECT COUNT(*) FROM relation").fetchone()[0]
    summary = {
        "entities": entities,
        "relations": relations,
        "seed_edges": seeded_edges,
        "llm_edges": llm_edges,
        "heuristic_edges": heur_edges,
        "docs_via_llm": docs_with_llm,
    }
    log.info("graph built: %s", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.graph.entities", description="Build the knowledge graph")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="cap documents processed")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from ..db import get_connection, migrate

    migrate()
    conn = get_connection()
    summary = build_graph(conn, use_llm=not args.no_llm, limit=args.limit)
    print(
        f"entities={summary['entities']} relations={summary['relations']} "
        f"(seed={summary['seed_edges']} llm={summary['llm_edges']} "
        f"heuristic={summary['heuristic_edges']})"
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
