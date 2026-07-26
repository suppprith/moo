"""Graph store + traversal.

Read helpers over the `entity` / `relation` tables. No graph database — at
corpus scale (tens of entities, low-hundreds of edges) SQLite recursive CTEs are
plenty. Relations are stored directed (subject → object) but exploration treats
them as **undirected**: an edge is materialized both ways inside each traversal
so `neighbors`/`subgraph`/`path` walk the graph freely while the returned edge
still records its real direction.

Public helpers:
- ``resolve(conn, name)``            entity id from canonical name or alias
- ``neighbors(conn, id, ...)``       one hop, with optional relation-type filter
- ``subgraph(conn, id, depth, ...)`` BFS neighborhood -> {nodes, edges} + provenance
- ``shortest_path(conn, a, b, ...)`` node/edge path between two entities

CLI:  ``uv run python -m app.graph.store neighbors Postgres``
      ``uv run python -m app.graph.store subgraph Redis --depth 2``
      ``uv run python -m app.graph.store path Postgres MySQL``
"""

from __future__ import annotations

import argparse
import json
import sqlite3

_UNDIRECTED = """
    edges(a, b, rid) AS (
        SELECT subject_entity_id, object_entity_id, id FROM relation{where}
        UNION ALL
        SELECT object_entity_id, subject_entity_id, id FROM relation{where}
    )
"""


def _edge_cte(rel_types: list[str] | None) -> tuple[str, list]:
    """The undirected `edges` CTE body plus its bound params (type filter x2)."""
    if rel_types:
        where = " WHERE type IN (" + ",".join("?" * len(rel_types)) + ")"
        return _UNDIRECTED.format(where=where), [*rel_types, *rel_types]
    return _UNDIRECTED.format(where=""), []


def resolve(conn: sqlite3.Connection, name: str) -> int | None:
    """Entity id for a canonical name or any alias (case-insensitive)."""
    name = (name or "").strip().lower()
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM entity WHERE lower(canonical_name) = ?", (name,)
    ).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "SELECT entity_id AS id FROM entity_alias WHERE lower(alias) = ?", (name,)
    ).fetchone()
    return row["id"] if row else None


def _nodes(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    qmarks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, canonical_name AS name, type, description FROM entity WHERE id IN ({qmarks})",
        ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _edges_among(conn: sqlite3.Connection, ids: list[int], rel_types: list[str] | None) -> list[dict]:
    """Every relation whose both endpoints are inside `ids` (with provenance)."""
    if len(ids) < 2:
        return []
    qmarks = ",".join("?" * len(ids))
    sql = (
        "SELECT r.id, r.subject_entity_id AS source, r.object_entity_id AS target, "
        "       r.type, r.strength, r.document_id, d.url AS document_url, d.title AS document_title "
        "FROM relation r LEFT JOIN document d ON d.id = r.document_id "
        f"WHERE r.subject_entity_id IN ({qmarks}) AND r.object_entity_id IN ({qmarks})"
    )
    params: list = [*ids, *ids]
    if rel_types:
        sql += " AND r.type IN (" + ",".join("?" * len(rel_types)) + ")"
        params += rel_types
    out = []
    for r in conn.execute(sql, params):
        provenance = (
            {"document_id": r["document_id"], "url": r["document_url"], "title": r["document_title"]}
            if r["document_id"] is not None
            else None
        )
        out.append({
            "id": r["id"], "source": r["source"], "target": r["target"],
            "type": r["type"], "strength": r["strength"], "provenance": provenance,
        })
    return out


def neighbors(
    conn: sqlite3.Connection, entity_id: int, *, rel_types: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """One-hop edges touching `entity_id`, each with the neighbor node inlined."""
    edge_cte, params = _edge_cte(rel_types)
    sql = (
        "WITH RECURSIVE " + edge_cte
        + " SELECT e.rid, e.b AS neighbor, r.subject_entity_id AS source, "
        "        r.object_entity_id AS target, r.type, r.strength, "
        "        en.canonical_name AS neighbor_name, en.type AS neighbor_type "
        " FROM edges e JOIN relation r ON r.id = e.rid "
        " JOIN entity en ON en.id = e.b "
        " WHERE e.a = ?"
    )
    params = [*params, entity_id]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    seen: set[int] = set()
    out = []
    for r in conn.execute(sql, params):
        if r["rid"] in seen:
            continue
        seen.add(r["rid"])
        out.append({
            "id": r["rid"], "source": r["source"], "target": r["target"],
            "type": r["type"], "strength": r["strength"],
            "neighbor": {"id": r["neighbor"], "name": r["neighbor_name"], "type": r["neighbor_type"]},
        })
    return out


def _reachable(
    conn: sqlite3.Connection, entity_id: int, depth: int, rel_types: list[str] | None, node_cap: int
) -> list[int]:
    """BFS node ids within `depth` hops of `entity_id`, nearest first, capped."""
    edge_cte, params = _edge_cte(rel_types)
    sql = (
        "WITH RECURSIVE " + edge_cte + ", "
        "reach(node, depth) AS ( "
        "   SELECT ?, 0 "
        "   UNION "
        "   SELECT e.b, r.depth + 1 FROM reach r JOIN edges e ON e.a = r.node WHERE r.depth < ? "
        ") SELECT node, MIN(depth) AS depth FROM reach GROUP BY node ORDER BY depth, node"
    )
    rows = conn.execute(sql, [*params, entity_id, depth]).fetchall()
    return [r["node"] for r in rows[:node_cap]]


def subgraph(
    conn: sqlite3.Connection, entity_id: int, *, depth: int = 1,
    rel_types: list[str] | None = None, node_cap: int = 60,
) -> dict:
    """Neighborhood of `entity_id` out to `depth` hops as {nodes, edges} in one
    call. Nodes are capped (nearest first); edges are every relation among the
    kept nodes, each carrying document provenance. Center id is echoed back."""
    ids = _reachable(conn, entity_id, depth, rel_types, node_cap)
    if entity_id not in ids:
        ids = [entity_id] + ids
    return {
        "center": entity_id,
        "nodes": _nodes(conn, ids),
        "edges": _edges_among(conn, ids, rel_types),
    }


def shortest_path(
    conn: sqlite3.Connection, a_id: int, b_id: int, *, max_depth: int = 5,
    rel_types: list[str] | None = None,
) -> dict | None:
    """Shortest undirected path a→b as {nodes, edges}, or None if unreachable
    within `max_depth`. Cycles are pruned by checking the accumulated path."""
    if a_id == b_id:
        return {"nodes": _nodes(conn, [a_id]), "edges": []}
    edge_cte, params = _edge_cte(rel_types)
    sql = (
        "WITH RECURSIVE " + edge_cte + ", "
        "paths(node, depth, ids) AS ( "
        "   SELECT ?, 0, ',' || ? || ',' "
        "   UNION ALL "
        "   SELECT e.b, p.depth + 1, p.ids || e.b || ',' "
        "   FROM paths p JOIN edges e ON e.a = p.node "
        "   WHERE p.depth < ? AND instr(p.ids, ',' || e.b || ',') = 0 "
        ") SELECT ids FROM paths WHERE node = ? ORDER BY depth LIMIT 1"
    )
    row = conn.execute(sql, [*params, a_id, a_id, max_depth, b_id]).fetchone()
    if not row:
        return None
    node_ids = [int(x) for x in row["ids"].strip(",").split(",") if x]
    step_types = None if rel_types is None else rel_types
    node_set = node_ids
    edges = _edges_among(conn, node_set, step_types)
    step = {(min(a, b), max(a, b)) for a, b in zip(node_ids, node_ids[1:], strict=False)}
    path_edges = [e for e in edges if (min(e["source"], e["target"]), max(e["source"], e["target"])) in step]
    ordered_nodes = {n["id"]: n for n in _nodes(conn, node_ids)}
    return {
        "nodes": [ordered_nodes[i] for i in node_ids if i in ordered_nodes],
        "edges": path_edges,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.graph.store", description="Traverse the knowledge graph")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_n = sub.add_parser("neighbors")
    p_n.add_argument("entity")
    p_n.add_argument("--types", nargs="*")

    p_s = sub.add_parser("subgraph")
    p_s.add_argument("entity")
    p_s.add_argument("--depth", type=int, default=1)
    p_s.add_argument("--types", nargs="*")

    p_p = sub.add_parser("path")
    p_p.add_argument("a")
    p_p.add_argument("b")
    p_p.add_argument("--max-depth", type=int, default=5)

    args = parser.parse_args(argv)

    from ..db import get_connection

    conn = get_connection()

    def _need(name: str) -> int:
        eid = resolve(conn, name)
        if eid is None:
            raise SystemExit(f"unknown entity: {name!r}")
        return eid

    if args.cmd == "neighbors":
        print(json.dumps(neighbors(conn, _need(args.entity), rel_types=args.types), indent=2))
    elif args.cmd == "subgraph":
        print(json.dumps(
            subgraph(conn, _need(args.entity), depth=args.depth, rel_types=args.types), indent=2
        ))
    elif args.cmd == "path":
        result = shortest_path(conn, _need(args.a), _need(args.b), max_depth=args.max_depth)
        print(json.dumps(result, indent=2) if result else "no path")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
