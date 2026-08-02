"use client";

import { useEffect, useRef } from "react";
import type { Source } from "@/lib/types";
import type { PanelTarget } from "./SourcePanel";
import { glyphChar, glyphColor, hostOf, sourceLabel, trustColor } from "@/lib/sources";

function TrustMeter({ score }: { score: number | null }) {
  if (score == null) return null;
  return (
    <span className="trust" title={`source trust ${score.toFixed(2)}`}>
      <span className="bar">
        <i style={{ width: `${Math.round(score * 100)}%`, background: trustColor(score) }} />
      </span>
      <span className="val">{score.toFixed(2)}</span>
    </span>
  );
}

export function SourceList({
  sources,
  onOpen,
  selected = -1,
}: {
  sources: Source[];
  onOpen?: (target: PanelTarget) => void;
  /** Index moved by j/k; -1 when the keyboard is not driving. */
  selected?: number;
}) {
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (selected < 0) return;
    const row = listRef.current?.querySelector<HTMLElement>("[data-selected='true']");
    row?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  if (sources.length === 0)
    return <p className="empty">No results in the corpus for this query.</p>;

  return (
    <ul className="results" ref={listRef}>
      {sources.map((s, i) => {
        const host = hostOf(s.document_url);
        const excerpt = s.highlights?.[0];
        return (
          <li className="result" data-selected={i === selected} key={s.chunk_id}>
            <span className="glyph" style={{ background: glyphColor(s.source_type) }} aria-hidden>
              {glyphChar(s.source_type, host)}
            </span>
            <div className="body">
              <div className="r-headline">
                {onOpen ? (
                  <button
                    className="r-title as-button"
                    onClick={() =>
                      onOpen({
                        chunkId: s.chunk_id,
                        spans: s.highlights,
                        label: s.title ?? host,
                      })
                    }
                    title="Show the excerpt this came from"
                  >
                    {s.title || host}
                  </button>
                ) : (
                  <span className="r-title">{s.title || host}</span>
                )}
                <a
                  className="r-out"
                  href={s.url_anchor || s.document_url}
                  target="_blank"
                  rel="noreferrer"
                  title="Open at the source"
                  aria-label="Open at the source"
                >
                  ↗
                </a>
              </div>
              <div className="r-meta">
                <span className="r-type">{sourceLabel(s.source_type)}</span>
                <span className="sep">·</span>
                <span className="r-host">{host}</span>
                <TrustMeter score={s.trust_score} />
                {s.suspicious && (
                  <span className="r-flag" title="This page matched prompt-injection patterns">
                    flagged
                  </span>
                )}
                {s.version_outdated && (
                  <span className="r-flag" title="Describes an older version than you asked about">
                    older version
                  </span>
                )}
              </div>
              {excerpt ? (
                <div className="r-snippet">{excerpt}</div>
              ) : (
                s.heading && <div className="r-snippet">{s.heading}</div>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
