"use client";

import { useState } from "react";
import type { Claim, Evidence, Relation, Source } from "@/lib/types";
import type { PanelTarget } from "./SourcePanel";
import { hostOf } from "@/lib/sources";
import { Caret } from "./icons";

const REL_ORDER: Relation[] = ["supports", "contradicts", "explains"];
const REL_LABEL: Record<Relation, string> = {
  supports: "Supports",
  contradicts: "Contradicts",
  explains: "Explains",
};

function confClass(c: number | null): "high" | "mid" | "low" {
  if (c == null) return "low";
  return c >= 0.66 ? "high" : c >= 0.4 ? "mid" : "low";
}

function EvidenceChip({
  e,
  claimText,
  onOpen,
}: {
  e: Evidence;
  claimText: string;
  onOpen?: (target: PanelTarget) => void;
}) {
  const host = e.document_url ? hostOf(e.document_url) : `chunk ${e.chunk_id}`;
  const inner = (
    <>
      <span>{host}</span>
      {e.strength != null && <span className="strength">{e.strength.toFixed(2)}</span>}
    </>
  );
  // Every evidence edge opens to the excerpt it is an edge to, with the part
  // that matches the claim marked.
  if (onOpen)
    return (
      <button
        className="chip as-button"
        onClick={() => onOpen({ chunkId: e.chunk_id, focus: claimText, label: host })}
        title="Show the excerpt behind this evidence"
      >
        {inner}
      </button>
    );
  return e.document_url ? (
    <a className="chip" href={e.document_url} target="_blank" rel="noreferrer">
      {inner}
    </a>
  ) : (
    <span className="chip">{inner}</span>
  );
}

function ClaimCard({ claim, onOpen }: { claim: Claim; onOpen?: (t: PanelTarget) => void }) {
  const [open, setOpen] = useState(false);
  const grouped = REL_ORDER.map((rel) => ({
    rel,
    items: claim.evidence.filter((e) => e.relation === rel),
  })).filter((g) => g.items.length > 0);
  const total = claim.evidence.length;

  return (
    <div className={`claim${claim.disputed ? " disputed" : ""}`}>
      <div className="head">
        <div className="c-text">{claim.text}</div>
        <div className="badges">
          {claim.disputed && <span className="pill-disputed">Disputed</span>}
          <span className={`conf ${confClass(claim.confidence)}`}>
            <span className="cdot" />
            {claim.confidence != null ? `${Math.round(claim.confidence * 100)}%` : "—"}
          </span>
        </div>
      </div>

      {total > 0 && (
        <button className="expander" data-open={open} onClick={() => setOpen((v) => !v)}>
          <Caret />
          {open ? "Hide" : "Show"} evidence · {total} source{total === 1 ? "" : "s"}
        </button>
      )}

      {open && (
        <div className="evidence">
          {grouped.map((g) => (
            <div className={`ev-group ev-${g.rel}`} key={g.rel}>
              <div className="ev-label">
                <span className={`ev-dot ${g.rel}`} />
                {REL_LABEL[g.rel]} · {g.items.length}
              </div>
              <div className="chips">
                {g.items.map((e, i) => (
                  <EvidenceChip
                    e={e}
                    claimText={claim.text}
                    onOpen={onOpen}
                    key={`${e.chunk_id}-${i}`}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ClaimList({
  claims,
  onOpen,
}: {
  claims: Claim[];
  sources?: Source[];
  onOpen?: (target: PanelTarget) => void;
}) {
  if (claims.length === 0)
    return (
      <p className="empty">
        No claims extracted. Try a comparison or troubleshooting query.
      </p>
    );
  return (
    <div className="claims">
      {claims.map((c) => (
        <ClaimCard claim={c} onOpen={onOpen} key={c.id} />
      ))}
    </div>
  );
}
