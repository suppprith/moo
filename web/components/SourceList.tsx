import type { Source } from "@/lib/types";
import {
  glyphChar,
  glyphColor,
  hostOf,
  sourceLabel,
  trustColor,
} from "@/lib/sources";

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

export function SourceList({ sources }: { sources: Source[] }) {
  if (sources.length === 0)
    return <p className="empty">No results in the corpus for this query.</p>;

  return (
    <ul className="results">
      {sources.map((s) => {
        const host = hostOf(s.document_url);
        return (
          <li className="result" key={s.chunk_id}>
            <span
              className="glyph"
              style={{ background: glyphColor(s.source_type) }}
              aria-hidden
            >
              {glyphChar(s.source_type, host)}
            </span>
            <div className="body">
              <a href={s.url_anchor || s.document_url} target="_blank" rel="noreferrer">
                <span className="r-title">{s.title || host}</span>
              </a>
              <div className="r-meta">
                <span className="r-type">{sourceLabel(s.source_type)}</span>
                <span className="sep">·</span>
                <span className="r-host">{host}</span>
                <TrustMeter score={s.trust_score} />
              </div>
              {s.heading && <div className="r-snippet">{s.heading}</div>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
