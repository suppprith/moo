"""Subgraph-for-query assembly.

`graph_for_query(conn, q)` turns a natural-language query into a bounded,
renderable graph:

1. Query understanding resolves the query to seed entities (`app.understand`).
2. Each seed's neighborhood is merged into one capped {nodes, edges} graph.
3. Claims are attached — to nodes (claims about that entity) and to edges
   (claims mentioning *both* endpoints, i.e. about the relationship itself),
   alongside each edge's document provenance. That claim↔entity association
   lives in `claim_entity`, populated here by matching entity aliases against
   claim text.

`expand_node(conn, node)` returns the next ring for one node, for click-to-expand.

This module has no LLM calls — it reads the graph the extraction pass built.
"""

from __future__ import annotations

import re
import sqlite3

from ..understand import understand
from . import store

DEFAULT_CAP = 60
DEFAULT_DEPTH = 1


def link_claims(conn: sqlite3.Connection) -> int:
    """Populate `claim_entity` by matching entity aliases against claim text.
    Idempotent (INSERT OR IGNORE). Returns the number of links written."""
    aliases = conn.execute(
        "SELECT entity_id, alias FROM entity_alias "
        "UNION SELECT id, canonical_name FROM entity"
    ).fetchall()
    patterns = sorted(
        ((r[0], re.compile(rf"\b{re.escape(r[1])}\b", re.I)) for r in aliases),
        key=lambda p: -len(p[1].pattern),
    )
    written = 0
    for claim in conn.execute("SELECT id, text FROM claim"):
        for entity_id, pat in patterns:
            if pat.search(claim["text"]):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO claim_entity (claim_id, entity_id) VALUES (?, ?)",
                    (claim["id"], entity_id),
                )
                written += cur.rowcount
    conn.commit()
    return written


def _claims_by_entity(conn: sqlite3.Connection, ids: list[int]) -> dict[int, list[dict]]:
    if not ids:
        return {}
    qmarks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT ce.entity_id, c.id, c.text, c.confidence, c.disputed
        FROM claim_entity ce JOIN claim c ON c.id = ce.claim_id
        WHERE ce.entity_id IN ({qmarks})
        ORDER BY c.confidence DESC
        """,
        ids,
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["entity_id"], []).append({
            "id": r["id"], "text": r["text"],
            "confidence": r["confidence"], "disputed": bool(r["disputed"]),
        })
    return out


def _attach_claims(conn: sqlite3.Connection, graph: dict) -> dict:
    """Fold claims into nodes (per entity) and edges (per relationship)."""
    node_ids = [n["id"] for n in graph["nodes"]]
    by_entity = _claims_by_entity(conn, node_ids)
    for node in graph["nodes"]:
        node["claims"] = by_entity.get(node["id"], [])
    all_claims: dict[int, dict] = {}
    for claims in by_entity.values():
        for c in claims:
            all_claims[c["id"]] = c
    for edge in graph["edges"]:
        src = {c["id"] for c in by_entity.get(edge["source"], [])}
        tgt = {c["id"] for c in by_entity.get(edge["target"], [])}
        shared = src & tgt
        edge["evidence"] = {
            "provenance": edge.get("provenance"),
            "claims": [all_claims[cid] for cid in shared],
        }
    graph["claims"] = sorted(
        all_claims.values(), key=lambda c: (c["confidence"] is None, -(c["confidence"] or 0))
    )
    return graph


def _merge(into: dict, other: dict) -> None:
    seen_nodes = {n["id"] for n in into["nodes"]}
    for n in other["nodes"]:
        if n["id"] not in seen_nodes:
            into["nodes"].append(n)
            seen_nodes.add(n["id"])
    seen_edges = {e["id"] for e in into["edges"]}
    for e in other["edges"]:
        if e["id"] not in seen_edges:
            into["edges"].append(e)
            seen_edges.add(e["id"])


def graph_for_query(
    conn: sqlite3.Connection, q: str, *, depth: int = DEFAULT_DEPTH, cap: int = DEFAULT_CAP
) -> dict:
    """Resolve `q` to seed entities and return their merged, claim-annotated
    neighborhood: {query, seeds, nodes, edges, claims}. Empty graph (with a note)
    when the query names nothing in the graph."""
    u = understand(q, conn)
    seeds: list[int] = []
    for name in u.entities:
        eid = store.resolve(conn, name)
        if eid is not None and eid not in seeds:
            seeds.append(eid)

    graph = {"query": q, "seeds": seeds, "intent": u.intent, "nodes": [], "edges": []}
    if not seeds:
        return _attach_claims(conn, graph) | {"note": "no known entities in query"}

    per_seed_cap = max(cap // len(seeds), 8)
    for eid in seeds:
        sub = store.subgraph(conn, eid, depth=depth, node_cap=per_seed_cap)
        _merge(graph, sub)
        if len(graph["nodes"]) >= cap:
            graph["nodes"] = graph["nodes"][:cap]
            kept = {n["id"] for n in graph["nodes"]}
            graph["edges"] = [
                e for e in graph["edges"] if e["source"] in kept and e["target"] in kept
            ]
            break
    return _attach_claims(conn, graph)


def expand_node(
    conn: sqlite3.Connection, node: str, *, rel_types: list[str] | None = None, limit: int | None = None
) -> dict | None:
    """The next ring for a node (id or name): its neighbors as {node, nodes,
    edges}, claim-annotated. None if the node is unknown."""
    eid = store.resolve(conn, node) if not node.isdigit() else int(node)
    if eid is None:
        return None
    center = conn.execute(
        "SELECT id, canonical_name AS name, type, description FROM entity WHERE id = ?", (eid,)
    ).fetchone()
    if center is None:
        return None
    ring = store.neighbors(conn, eid, rel_types=rel_types, limit=limit)
    node_ids = [eid] + [e["neighbor"]["id"] for e in ring]
    graph = {
        "center": dict(center),
        "nodes": store._nodes(conn, node_ids),
        "edges": store._edges_among(conn, node_ids, rel_types),
    }
    return _attach_claims(conn, graph)
