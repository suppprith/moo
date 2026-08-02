"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { CommandPalette, ShortcutHelp } from "@/components/CommandPalette";
import type { Command } from "@/lib/shortcuts";
import { SHORTCUTS, isSystemChord, isTypingTarget, moveSelection } from "@/lib/shortcuts";

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
  const [selected, setSelected] = useState(-1);
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const onModeRef = useRef<(m: Mode) => void>(() => {});
  const goHomeRef = useRef<() => void>(() => {});

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
    setSelected(-1);
    run(q, mode);
  };

  const onMode = (m: Mode) => {
    setMode(m);
    if (query) run(query, m);
  };

  const openSelected = useCallback(
    (newTab: boolean) => {
      const source = data?.sources[selected];
      if (!source) return;
      if (newTab) {
        window.open(source.url_anchor || source.document_url, "_blank", "noreferrer");
        return;
      }
      setPanel({
        chunkId: source.chunk_id,
        spans: source.highlights,
        label: source.title ?? undefined,
      });
    },
    [data, selected],
  );

  const commands: Command[] = useMemo(() => {
    const list: Command[] = [
      { id: "focus", label: "Search something else", keys: "/", group: "Search" },
      { id: "mode:raw", label: "Raw results", hint: "no LLM calls", keys: "1", group: "View" },
      { id: "mode:claims", label: "Claims and evidence", keys: "2", group: "View" },
      { id: "mode:full", label: "Cited answer", keys: "3", group: "View" },
      { id: "docs", label: "Docs", group: "Go" },
      { id: "why", label: "Why moo", group: "Go" },
      { id: "home", label: "Back to the start", group: "Go" },
      { id: "help", label: "Keyboard shortcuts", keys: "?", group: "View" },
    ];
    if (query) {
      list.splice(1, 0,
        { id: "research", label: "Deep research this query", keys: "d", group: "Search" },
        { id: "graph", label: "Toggle the evidence graph", keys: "g", group: "View" });
    }
    return list;
  }, [query]);

  const runCommand = useCallback(
    (id: string) => {
      setPalette(false);
      if (id.startsWith("mode:")) return onModeRef.current(id.slice(5) as Mode);
      if (id === "research" && query) return setResearch(query);
      if (id === "graph" && query) return setGraph((g) => (g ? null : query));
      if (id === "home") return goHomeRef.current();
      if (id === "help") return setHelp(true);
      if (id === "docs") return void (window.location.href = "/docs");
      if (id === "why") return void (window.location.href = "/why");
      if (id === "focus") {
        const box = document.querySelector<HTMLInputElement>(".searchbox input");
        box?.focus();
        box?.select();
      }
    },
    [query],
  );

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((open) => !open);
        return;
      }
      if (palette || isTypingTarget(event.target) || isSystemChord(event)) return;

      const sources = data?.sources ?? [];
      switch (event.key) {
        case "j":
          event.preventDefault();
          setSelected((i) => moveSelection(i, 1, sources.length));
          break;
        case "k":
          event.preventDefault();
          setSelected((i) => moveSelection(i, -1, sources.length));
          break;
        case "Enter":
          if (selected >= 0) {
            event.preventDefault();
            openSelected(false);
          }
          break;
        case "o":
          if (selected >= 0) {
            event.preventDefault();
            openSelected(true);
          }
          break;
        case "1":
          onModeRef.current("raw");
          break;
        case "2":
          onModeRef.current("claims");
          break;
        case "3":
          onModeRef.current("full");
          break;
        case "d":
          if (query) setResearch(query);
          break;
        case "g":
          if (query) setGraph((g) => (g ? null : query));
          break;
        case "?":
          event.preventDefault();
          setHelp((open) => !open);
          break;
        case "Escape":
          if (help) setHelp(false);
          else if (panel) setPanel(null);
          else setSelected(-1);
          break;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [data, help, openSelected, palette, panel, query, selected]);

  const goHome = () => {
    setSearched(false);
    setData(null);
    setError(null);
    setQuery("");
    setMode("raw");
    setResearch(null);
    setGraph(null);
  };

  onModeRef.current = onMode;
  goHomeRef.current = goHome;

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
            <SourceList sources={data.sources} onOpen={setPanel} selected={selected} />
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
      {palette && (
        <CommandPalette
          commands={commands}
          onRun={runCommand}
          onClose={() => setPalette(false)}
        />
      )}
      {help && <ShortcutHelp shortcuts={SHORTCUTS} onClose={() => setHelp(false)} />}
    </div>
  );
}
