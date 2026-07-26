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

export interface SubQuestion {
  id: number;
  question: string;
  depends_on: number[];
}

export interface ResearchStep {
  step: number;
  sub_question_id: number | null;
  query: string;
  reason: "plan" | "gap" | "contradiction";
  claims: number;
  new_claims: number;
  disputed: number;
}

export interface Finding {
  claim: string; // clm_<id> handle
  text: string;
  confidence: number | null;
  disputed: boolean;
  sub_question_id: number | null;
  citations: number[]; // source indices (S#)
}

export interface DisputedPoint {
  claim: string;
  text: string;
  supports: number[];
  contradicts: number[];
}

export interface ResearchSource {
  index: number;
  document_id: number;
  url: string | null;
  title: string | null;
  source_type: string;
  trust_score: number | null;
  handle: string; // doc_<id>
  trust_tier: string;
}

export interface Groundedness {
  findings_total: number;
  findings_grounded: number;
  pct_grounded: number;
  well_supported: number;
  answer_citations_valid: boolean;
  ungrounded: string[];
}

export interface ResearchReport {
  question: string;
  run_id?: string;
  status?: "running" | "partial" | "done" | "failed";
  executive_answer: string;
  findings: Finding[];
  disputed_points: DisputedPoint[];
  open_questions: string[];
  sources: ResearchSource[];
  groundedness: Groundedness;
  generator: string;
  steps?: ResearchStep[];
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
