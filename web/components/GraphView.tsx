"use client";

import { useEffect, useMemo, useState } from "react";
import { ApiError, expandNode, graphForQuery } from "@/lib/api";
import type { Graph, GraphEdge, GraphNode } from "@/lib/types";

const W = 760;
const H = 480;

const NODE_COLOR: Record<string, string> = {
  tool: "var(--accent)",
  library: "#8b5cf6",
  concept: "#2f7ae5",
  algorithm: "#e8792b",
};
const EDGE_COLOR: Record<string, string> = {
  "alternative-to": "var(--explains)",
  "part-of": "var(--accent)",
  "built-with": "var(--supports)",
  "used-by": "var(--ink-3)",
  replaces: "var(--contradicts)",
};
const nodeColor = (t: string) => NODE_COLOR[t] ?? "var(--ink-3)";
const edgeColor = (t: string) => EDGE_COLOR[t] ?? "var(--border-strong)";

type Pos = Map<number, { x: number; y: number }>;

// Deterministic radial layout: BFS levels from the query's seed entities,
// placed on concentric rings. Readable every time, no physics needed.
function radialLayout(nodes: GraphNode[], edges: GraphEdge[], seeds: number[]): Pos {
  const ids = new Set(nodes.map((n) => n.id));
  const adj = new Map<number, number[]>();
  nodes.forEach((n) => adj.set(n.id, []));
  edges.forEach((e) => {
    if (ids.has(e.source) && ids.has(e.target)) {
      adj.get(e.source)!.push(e.target);
      adj.get(e.target)!.push(e.source);
    }
  });
  const level = new Map<number, number>();
  let frontier = seeds.filter((s) => ids.has(s));
  if (frontier.length === 0 && nodes.length) frontier = [nodes[0].id];
  frontier.forEach((s) => level.set(s, 0));
  let cur = frontier;
  let lvl = 0;
  while (cur.length) {
    const next: number[] = [];
    for (const id of cur)
      for (const nb of adj.get(id) ?? [])
        if (!level.has(nb)) {
          level.set(nb, lvl + 1);
          next.push(nb);
        }
    cur = next;
    lvl++;
  }
  nodes.forEach((n) => { if (!level.has(n.id)) level.set(n.id, lvl); });

  const byLevel = new Map<number, number[]>();
  nodes.forEach((n) => {
    const l = level.get(n.id)!;
    if (!byLevel.has(l)) byLevel.set(l, []);
    byLevel.get(l)!.push(n.id);
  });
  const maxL = Math.max(0, ...byLevel.keys());
  const step = (Math.min(W, H) * 0.42) / Math.max(maxL, 1);
  const pos: Pos = new Map();
  for (const [l, group] of byLevel) {
    const r = l === 0 && group.length === 1 ? 0 : l === 0 ? Math.min(W, H) * 0.09 : l * step;
    group.forEach((id, i) => {
      const a = (2 * Math.PI * i) / group.length - Math.PI / 2 + l * 0.5;
      pos.set(id, { x: W / 2 + Math.cos(a) * r, y: H / 2 + Math.sin(a) * r });
    });
  }
  return pos;
}

function mergeGraph(base: Graph, add: Graph): Graph {
  const nodeIds = new Set(base.nodes.map((n) => n.id));
  const edgeIds = new Set(base.edges.map((e) => e.id));
  const nodes = [...base.nodes];
  const edges = [...base.edges];
  add.nodes.forEach((n) => { if (!nodeIds.has(n.id)) { nodeIds.add(n.id); nodes.push(n); } });
  add.edges.forEach((e) => { if (!edgeIds.has(e.id)) { edgeIds.add(e.id); edges.push(e); } });
  return { ...base, nodes, edges };
}

export function GraphView({ query }: { query: string }) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setGraph(null);
    setSelected(null);
    setExpanded(new Set());
    setError(null);
    graphForQuery(query, { depth: 1, cap: 24, signal: ctrl.signal })
      .then((g) => setGraph(g))
      .catch((e) => {
        if ((e as Error).name === "AbortError") return;
        setError(e instanceof ApiError ? e.message : "Could not load the graph.");
      })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [query]);

  const pos = useMemo(
    () => (graph ? radialLayout(graph.nodes, graph.edges, graph.seeds ?? []) : new Map()),
    [graph],
  );

  const onNode = async (n: GraphNode) => {
    setSelected(n.id);
    if (expanded.has(n.id)) return;
    setExpanded((prev) => new Set(prev).add(n.id));
    try {
      const ring = await expandNode(String(n.id));
      setGraph((g) => (g ? mergeGraph(g, ring) : g));
    } catch {
      /* expansion is best-effort */
    }
  };

  if (loading) return <div className="graph-status">Building the evidence graph…</div>;
  if (error) return <div className="error">{error}</div>;
  if (!graph || graph.nodes.length === 0)
    return <div className="graph-status">No known entities in this query’s graph. Try a comparison (e.g. “Postgres vs Redis”).</div>;

  const sel = graph.nodes.find((n) => n.id === selected) ?? null;

  return (
    <div className="graph">
      <svg className="graph-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="evidence graph">
        {graph.edges.map((e) => {
          const a = pos.get(e.source);
          const b = pos.get(e.target);
          if (!a || !b) return null;
          const active = selected != null && (e.source === selected || e.target === selected);
          return (
            <line
              key={e.id}
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              stroke={edgeColor(e.type)}
              strokeWidth={active ? 2.4 : 1.4}
              strokeOpacity={selected == null || active ? 0.75 : 0.2}
            >
              <title>{e.type}</title>
            </line>
          );
        })}
        {graph.nodes.map((n) => {
          const p = pos.get(n.id);
          if (!p) return null;
          const isSel = n.id === selected;
          const r = (graph.seeds ?? []).includes(n.id) ? 11 : 8;
          return (
            <g key={n.id} className="g-node" onClick={() => onNode(n)} style={{ cursor: "pointer" }}>
              <circle
                cx={p.x} cy={p.y} r={isSel ? r + 3 : r}
                fill={nodeColor(n.type)}
                stroke={isSel ? "var(--ink)" : "var(--panel)"}
                strokeWidth={isSel ? 2.5 : 2}
              />
              <text x={p.x} y={p.y - r - 6} textAnchor="middle" className="g-label">
                {n.name}
              </text>
            </g>
          );
        })}
      </svg>

      <div className="graph-legend">
        {Object.entries(EDGE_COLOR).map(([t, c]) => (
          <span key={t} className="leg"><i style={{ background: c }} />{t}</span>
        ))}
        <span className="leg-note">click a node to expand + see its claims</span>
      </div>

      {sel && (
        <div className="graph-detail">
          <div className="gd-head">
            <span className="gd-dot" style={{ background: nodeColor(sel.type) }} />
            <b>{sel.name}</b> <span className="gd-type">{sel.type}</span>
          </div>
          {sel.description && <p className="gd-desc">{sel.description}</p>}
          {sel.claims && sel.claims.length > 0 ? (
            <ul className="gd-claims">
              {sel.claims.map((c) => (
                <li key={c.id}>
                  <span className={`conf ${c.confidence != null && c.confidence >= 0.66 ? "high" : c.confidence != null && c.confidence >= 0.4 ? "mid" : "low"}`}>
                    <span className="cdot" />
                    {c.confidence != null ? `${Math.round(c.confidence * 100)}%` : "—"}
                  </span>
                  {c.disputed && <span className="pill-disputed">Disputed</span>}
                  {c.text}
                </li>
              ))}
            </ul>
          ) : (
            <p className="gd-empty">No claims attached to this entity yet.</p>
          )}
        </div>
      )}
    </div>
  );
}
