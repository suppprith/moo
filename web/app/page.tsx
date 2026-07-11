"use client";

import { useCallback, useRef, useState } from "react";
import { ApiError, search } from "@/lib/api";
import type { Mode, SearchResponse } from "@/lib/types";
import { Answer } from "@/components/Answer";
import { ClaimList } from "@/components/ClaimList";
import { ModeBar } from "@/components/ModeBar";
import { ResearchView } from "@/components/ResearchView";
import { SearchBox } from "@/components/SearchBox";
import { SourceList } from "@/components/SourceList";

const EXAMPLES = [
  "Postgres vs MySQL for a new web app",
  "Why is my Postgres query not using the index?",
  "Is SQLite good enough for production?",
  "Redis vs Postgres for a job queue",
];

function ResultSkeleton() {
  return (
    <div className="skeleton" aria-hidden>
      {Array.from({ length: 5 }).map((_, i) => (
        <div className="skel-row" key={i}>
          <div className="skel sq" />
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div className="skel line" style={{ width: "55%" }} />
            <div className="skel line" style={{ width: "30%", height: 9 }} />
            <div className="skel line" style={{ width: "85%", height: 9 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<Mode>("raw");
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);
  const [research, setResearch] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(async (q: string, m: Mode) => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    try {
      const res = await search(q, m, { signal: ctrl.signal });
      setData(res);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setError(e instanceof ApiError ? e.message : "Something went wrong.");
      setData(null);
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }, []);

  const onSearch = (q: string) => {
    setQuery(q);
    setSearched(true);
    setResearch(null);
    run(q, mode);
  };

  const onMode = (m: Mode) => {
    setMode(m);
    if (query) run(query, m);
  };

  const goHome = () => {
    setSearched(false);
    setData(null);
    setError(null);
    setQuery("");
    setMode("raw");
    setResearch(null);
  };

  // ---- home (pre-search) hero ----
  if (!searched) {
    return (
      <div className="shell">
        <div className="home">
          <h1 className="wordmark">moo</h1>
          <p className="tagline">
            CS/coding evidence search. Claims backed by typed evidence — supports,
            contradicts, explains — with confidence and source trust, not ten blue links.
          </p>
          <SearchBox loading={loading} size="lg" onSearch={onSearch} />
          <div className="examples">
            {EXAMPLES.map((ex) => (
              <button className="example" key={ex} onClick={() => onSearch(ex)}>
                {ex}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ---- results ----
  return (
    <div className="shell">
      <header className="topbar">
        <div className="inner">
          <div className="home-wordmark" onClick={goHome}>
            moo
          </div>
          <SearchBox initial={query} loading={loading} onSearch={onSearch} />
        </div>
      </header>

      <main className="content">
        {research !== null ? (
          <ResearchView question={research} onExit={() => setResearch(null)} />
        ) : (
          <>
        <div className="toolbar">
          {data && (
            <ModeBar
              mode={mode}
              intent={data.intent}
              elapsedMs={data.meta.elapsed_ms}
              counts={{ claims: data.claims.length }}
              onMode={onMode}
            />
          )}
          {query && (
            <button className="deep-btn" onClick={() => setResearch(query)}>
              Deep research →
            </button>
          )}
        </div>

        {error && <div className="error">{error}</div>}

        {loading && !data && <ResultSkeleton />}

        {data && !error && (
          <>
            {mode === "full" && data.answer && (
              <Answer
                text={data.answer}
                citations={data.citations}
                generator={data.meta.generator}
              />
            )}

            {mode !== "raw" && (
              <>
                <div className="section-label">
                  Claims <span className="count">{data.claims.length}</span>
                </div>
                <ClaimList claims={data.claims} sources={data.sources} />
              </>
            )}

            <div className="section-label">
              Sources <span className="count">{data.sources.length}</span>
            </div>
            <SourceList sources={data.sources} />
          </>
        )}
          </>
        )}
      </main>
    </div>
  );
}
