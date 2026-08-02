"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { API_BASE, ApiError, search } from "@/lib/api";
import type { Mode, SearchResponse } from "@/lib/types";
import { Answer } from "@/components/Answer";
import { ClaimList } from "@/components/ClaimList";
import { GraphView } from "@/components/GraphView";
import { ModeBar } from "@/components/ModeBar";
import { ResearchView } from "@/components/ResearchView";
import { SearchBox } from "@/components/SearchBox";
import { SourceList } from "@/components/SourceList";
import { SourcePanel } from "@/components/SourcePanel";
import type { PanelTarget } from "@/components/SourcePanel";

/** A public playground says so: the visitor is on someone else's quota. */
const DEMO = process.env.NEXT_PUBLIC_DEMO_MODE === "1";

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
  const [graph, setGraph] = useState<string | null>(null);
  const [panel, setPanel] = useState<PanelTarget | null>(null);
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
    setGraph(null);
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
    setGraph(null);
  };

  if (!searched) {
    return (
      <div className="shell">
        <div className="home">
          <h1 className="wordmark">moo</h1>
          <p className="tagline">
            Live search with an evidence layer. Claims backed by typed evidence — supports,
            contradicts, explains — with confidence and source trust, not ten blue links.
          </p>
          {DEMO && (
            <p className="demo-note">
              This is a shared demo instance, rate limited per visitor and running on someone
              else&apos;s hardware. <a href={`${API_BASE}/signup`}>Get a key</a> for your own quota,
              or <Link href="/docs/hosting">run it yourself</Link>.
            </p>
          )}
          <SearchBox loading={loading} size="lg" onSearch={onSearch} />
          <div className="examples">
            {EXAMPLES.map((ex) => (
              <button className="example" key={ex} onClick={() => onSearch(ex)}>
                {ex}
              </button>
            ))}
          </div>
          <div className="home-links">
            <Link href="/docs">Docs</Link>
            <Link href="/why">Why moo</Link>
            <a href="https://github.com/suppprith/moo">GitHub</a>
          </div>
        </div>
      </div>
    );
  }

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
          {query && (
            <button
              className={`deep-btn${graph !== null ? " active" : ""}`}
              onClick={() => setGraph((g) => (g ? null : query))}
            >
              Evidence graph {graph !== null ? "✕" : "→"}
            </button>
          )}
        </div>

        {error && <div className="error">{error}</div>}

        {graph !== null ? (
          <GraphView query={graph} />
        ) : (
        <>
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
                <ClaimList claims={data.claims} sources={data.sources} onOpen={setPanel} />
              </>
            )}

            <div className="section-label">
              Sources <span className="count">{data.sources.length}</span>
            </div>
            <SourceList sources={data.sources} onOpen={setPanel} />
          </>
        )}
        </>
        )}
          </>
        )}
      </main>

      {panel && (
        <SourcePanel target={panel} onClose={() => setPanel(null)} onNavigate={setPanel} />
      )}
    </div>
  );
}
