"use client";

import { useCallback, useRef, useState } from "react";
import { ApiError, search } from "@/lib/api";
import type { Mode, SearchResponse } from "@/lib/types";
import { ModeBar } from "@/components/ModeBar";
import { SearchBox } from "@/components/SearchBox";
import { SourceList } from "@/components/SourceList";

export default function Home() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<Mode>("raw");
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    run(q, mode);
  };

  const onMode = (m: Mode) => {
    setMode(m);
    if (query) run(query, m);
  };

  return (
    <main className="page">
      <h1 className="brand">
        moo<span> search</span>
      </h1>

      <SearchBox loading={loading} onSearch={onSearch} />

      {data && (
        <ModeBar
          mode={mode}
          intent={data.intent}
          elapsedMs={data.meta.elapsed_ms}
          onMode={onMode}
        />
      )}

      {error && <div className="error">{error}</div>}

      {data && !error && (
        <>
          {mode === "full" && data.answer && (
            <p style={{ marginTop: "1rem" }}>{data.answer}</p>
          )}

          {mode !== "raw" && (
            <div className="section-label">
              {data.claims.length} claim{data.claims.length === 1 ? "" : "s"}
              {data.graph.nodes.length > 0 &&
                ` · ${data.graph.nodes.length} graph nodes`}
            </div>
          )}

          <div className="section-label">Sources</div>
          <SourceList sources={data.sources} />
        </>
      )}

      {!data && !error && (
        <p className="hint" style={{ marginTop: "1.5rem" }}>
          Ask about PostgreSQL, MySQL, SQLite, or Redis. The default view is pure
          retrieval — no AI until you ask for it.
        </p>
      )}
    </main>
  );
}
