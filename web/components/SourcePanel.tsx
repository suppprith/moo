"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchChunk } from "@/lib/api";
import { highlightRanges } from "@/lib/highlight";
import type { ChunkDetail } from "@/lib/types";
import { hostOf, sourceLabel, trustColor } from "@/lib/sources";

/**
 * The panel behind every citation: the chunk's own text with the cited span
 * highlighted, who wrote it and when, and a link to the exact place it came
 * from rather than the top of the page.
 */

export type PanelTarget = {
  chunkId: number | string;
  /** Spans the API already picked out, when the source row carried them. */
  spans?: string[];
  /** A claim, when the panel was opened from evidence: the best-matching
   *  sentence gets highlighted, since there is no span to trust. */
  focus?: string;
  label?: string;
};

function Highlighted({ text, target }: { text: string; target: PanelTarget }) {
  const ranges = highlightRanges(text, target);
  if (ranges.length === 0) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  ranges.forEach(([start, end], i) => {
    if (start < cursor) return;
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(<mark key={i}>{text.slice(start, end)}</mark>);
    cursor = end;
  });
  parts.push(text.slice(cursor));
  return <>{parts}</>;
}

function tier(score: number | null): string {
  if (score == null) return "unrated";
  return score >= 0.75 ? "high trust" : score >= 0.5 ? "medium trust" : "low trust";
}

function when(published: string | null): string | null {
  if (!published) return null;
  const date = new Date(published);
  if (Number.isNaN(date.getTime())) return published;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function SourcePanel({
  target,
  onClose,
  onNavigate,
}: {
  target: PanelTarget;
  onClose: () => void;
  onNavigate: (t: PanelTarget) => void;
}) {
  const [chunk, setChunk] = useState<ChunkDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setChunk(null);
    setError(null);
    fetchChunk(target.chunkId, controller.signal)
      .then(setChunk)
      .catch((e) => {
        if ((e as Error).name === "AbortError") return;
        setError(e instanceof ApiError ? e.message : "Could not load this source.");
      });
    return () => controller.abort();
  }, [target.chunkId]);

  const escape = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [escape]);

  const host = chunk?.url ? hostOf(chunk.url) : null;
  const published = when(chunk?.published_at ?? null);

  return (
    <>
      <div className="panel-scrim" onClick={onClose} aria-hidden />
      <aside className="source-panel" role="dialog" aria-label="Source excerpt">
        <header className="sp-head">
          <div className="sp-titles">
            <div className="sp-title">{chunk?.title || target.label || "Source"}</div>
            {chunk && (
              <div className="sp-meta">
                <span>{sourceLabel(chunk.source_type)}</span>
                {host && <span className="sep">·</span>}
                {host && <span>{host}</span>}
                {published && <span className="sep">·</span>}
                {published && <span>{published}</span>}
              </div>
            )}
          </div>
          <button className="sp-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        {chunk && (
          <div className="sp-badges">
            <span
              className="sp-trust"
              style={{ borderColor: trustColor(chunk.trust_score ?? 0) }}
              title={
                chunk.trust_score != null
                  ? `trust ${chunk.trust_score.toFixed(2)}`
                  : "no trust score"
              }
            >
              <i style={{ background: trustColor(chunk.trust_score ?? 0) }} />
              {tier(chunk.trust_score)}
              {chunk.trust_score != null && ` ${chunk.trust_score.toFixed(2)}`}
            </span>
            {chunk.author_role && <span className="sp-role">{chunk.author_role}</span>}
            {!published && <span className="sp-undated">undated</span>}
          </div>
        )}

        {error && <p className="sp-error">{error}</p>}
        {!chunk && !error && <p className="sp-loading">Loading the excerpt…</p>}

        {chunk && (
          <>
            {chunk.heading && <div className="sp-heading">{chunk.heading}</div>}
            <div className="sp-text">
              <Highlighted text={chunk.text} target={target} />
            </div>

            <div className="sp-nav">
              {chunk.context.prev && (
                <button
                  onClick={() => onNavigate({ chunkId: chunk.context.prev!.id, label: chunk.title ?? undefined })}
                >
                  ← {chunk.context.prev.heading || "Previous section"}
                </button>
              )}
              {chunk.context.next && (
                <button
                  className="next"
                  onClick={() => onNavigate({ chunkId: chunk.context.next!.id, label: chunk.title ?? undefined })}
                >
                  {chunk.context.next.heading || "Next section"} →
                </button>
              )}
            </div>

            {chunk.url && (
              <a className="sp-open" href={chunk.url} target="_blank" rel="noreferrer">
                Open at the source ↗
              </a>
            )}
          </>
        )}
      </aside>
    </>
  );
}
