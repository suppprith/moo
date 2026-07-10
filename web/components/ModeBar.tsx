"use client";

import type { Mode } from "@/lib/types";

const MODES: { key: Mode; label: string; hint: string }[] = [
  { key: "raw", label: "Results", hint: "pure retrieval, no AI" },
  { key: "claims", label: "Claims", hint: "evidence + confidence" },
  { key: "full", label: "Answer", hint: "cited synthesis" },
];

export function ModeBar({
  mode,
  intent,
  elapsedMs,
  onMode,
}: {
  mode: Mode;
  intent?: string;
  elapsedMs?: number;
  onMode: (m: Mode) => void;
}) {
  return (
    <div className="modebar">
      <div className="seg" role="tablist" aria-label="Result mode">
        {MODES.map((m) => (
          <button
            key={m.key}
            role="tab"
            aria-selected={mode === m.key}
            data-active={mode === m.key}
            title={m.hint}
            onClick={() => onMode(m.key)}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className="meta">
        {intent && <span>{intent}</span>}
        {elapsedMs != null && <span> · {elapsedMs.toFixed(0)}ms</span>}
      </div>
    </div>
  );
}
