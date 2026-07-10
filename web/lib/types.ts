// Mirror of the api /search response contract (SUP-92, CONTRACT_VERSION "1.0").

export type Mode = "raw" | "claims" | "full";
export type Relation = "supports" | "contradicts" | "explains";

export interface Source {
  chunk_id: number;
  document_url: string;
  title: string | null;
  source_type: string;
  trust_score: number | null;
  heading: string | null;
  url_anchor: string;
  score: number;
}

export interface Evidence {
  relation: Relation;
  chunk_id: number;
  strength: number | null;
  document_url: string | null;
}

export interface Claim {
  id: number;
  text: string;
  confidence: number | null;
  disputed: boolean;
  evidence: Evidence[];
}

export interface GraphNode {
  id: number;
  name: string;
  type: string;
  description?: string | null;
  claims?: { id: number; text: string; confidence: number | null; disputed: boolean }[];
}

export interface GraphEdge {
  id: number;
  source: number;
  target: number;
  type: string;
  strength: number | null;
  provenance?: { document_id: number; url: string | null; title: string | null } | null;
  evidence?: { provenance: unknown; claims: { id: number; text: string }[] };
}

export interface Graph {
  query?: string;
  seeds?: number[];
  intent?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  claims?: Claim[];
  note?: string;
}

export interface Citation {
  index: number;
  document_id: number;
  url: string | null;
  title: string | null;
  source_type: string | null;
  trust_score: number | null;
}

export interface SearchResponse {
  query: string;
  mode: Mode;
  intent: string;
  answer: string | null;
  claims: Claim[];
  graph: Graph;
  sources: Source[];
  citations: Citation[];
  meta: {
    contract_version: string;
    entities?: string[];
    elapsed_ms?: number;
    generator?: string;
  };
}
