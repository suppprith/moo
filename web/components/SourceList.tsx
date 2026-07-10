import type { Source } from "@/lib/types";

function hostOf(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function SourceList({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return <p className="hint">No results.</p>;
  return (
    <ul className="sources">
      {sources.map((s) => (
        <li className="source" key={s.chunk_id}>
          <div className="row1">
            <a className="title" href={s.url_anchor || s.document_url} target="_blank" rel="noreferrer">
              {s.title || hostOf(s.document_url)}
            </a>
            <span className="badge">{s.source_type}</span>
            {s.trust_score != null && (
              <span className="trust" title="source trust score">
                trust {s.trust_score.toFixed(2)}
              </span>
            )}
          </div>
          {s.heading && <div className="snippet">{s.heading}</div>}
          <div className="urlline">{hostOf(s.document_url)}</div>
        </li>
      ))}
    </ul>
  );
}
