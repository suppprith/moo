"use client";

import type { Mode } from "@/lib/types";

const MODES: { key: Mode; label: string }[] = [
  { key: "raw", label: "Results" },
  { key: "claims", label: "Claims" },
  { key: "full", label: "Answer" },
];

export function ModeBar({
  mode,
  intent,
  elapsedMs,
  counts,
  onMode,
}: {
  mode: Mode;
  intent?: string;
  elapsedMs?: number;
  counts: { claims: number };
  onMode: (m: Mode) => void;
}) {
  return (
    <div className="tabs" role="tablist" aria-label="Result mode">
      {MODES.map((m) => (
        <button
          key={m.key}
          role="tab"
          aria-selected={mode === m.key}
          data-active={mode === m.key}
          className="tab"
          onClick={() => onMode(m.key)}
        >
          {m.label}
          {m.key === "claims" && counts.claims > 0 && (
            <span className="dot">{counts.claims}</span>
          )}
        </button>
      ))}
      <span className="spacer" />
      <div className="runmeta">
        {intent && <span className="chip-intent">{intent}</span>}
        {elapsedMs != null && <span>{formatMs(elapsedMs)}</span>}
      </div>
    </div>
  );
}

function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms.toFixed(0)}ms`;
}
