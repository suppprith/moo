"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, deepResearch } from "@/lib/api";
import type {
  Citation,
  DisputedPoint,
  Finding,
  ResearchReport,
  ResearchSource,
  ResearchStep,
  Source,
  SubQuestion,
} from "@/lib/types";
import { Answer } from "./Answer";
import { SourceList } from "./SourceList";

function confClass(c: number | null): "high" | "mid" | "low" {
  if (c == null) return "low";
  return c >= 0.66 ? "high" : c >= 0.4 ? "mid" : "low";
}

function asCitations(sources: ResearchSource[]): Citation[] {
  return sources.map((s) => ({
    index: s.index,
    document_id: s.document_id,
    url: s.url,
    title: s.title,
    source_type: s.source_type,
    trust_score: s.trust_score,
  }));
}

function asSources(sources: ResearchSource[]): Source[] {
  return sources.map((s) => ({
    chunk_id: s.index,
    document_url: s.url ?? "",
    title: s.title,
    source_type: s.source_type,
    trust_score: s.trust_score,
    heading: null,
    url_anchor: s.url ?? "",
    score: 0,
  }));
}

function Cites({ indices, byIndex }: { indices: number[]; byIndex: Map<number, ResearchSource> }) {
  return (
    <span className="cites">
      {indices.map((i) => {
        const s = byIndex.get(i);
        return s?.url ? (
          <a key={i} className="cite" href={s.url} target="_blank" rel="noreferrer" title={s.title ?? s.url}>
            S{i}
          </a>
        ) : (
          <span key={i} className="cite">S{i}</span>
        );
      })}
    </span>
  );
}

function FindingCard({ f, byIndex }: { f: Finding; byIndex: Map<number, ResearchSource> }) {
  return (
    <div className={`claim${f.disputed ? " disputed" : ""}`}>
      <div className="head">
        <div className="c-text">{f.text}</div>
        <div className="badges">
          {f.disputed && <span className="pill-disputed">Disputed</span>}
          <span className={`conf ${confClass(f.confidence)}`}>
            <span className="cdot" />
            {f.confidence != null ? `${Math.round(f.confidence * 100)}%` : "—"}
          </span>
        </div>
      </div>
      {f.citations.length > 0 && (
        <div className="finding-cites">
          <Cites indices={f.citations} byIndex={byIndex} />
        </div>
      )}
    </div>
  );
}

function DisputedCard({ dp, byIndex }: { dp: DisputedPoint; byIndex: Map<number, ResearchSource> }) {
  return (
    <div className="claim disputed">
      <div className="c-text">{dp.text}</div>
      <div className="disputed-sides">
        <div className="side supports">
          <span className="ev-dot supports" /> Supports <Cites indices={dp.supports} byIndex={byIndex} />
        </div>
        <div className="side contradicts">
          <span className="ev-dot contradicts" /> Contradicts <Cites indices={dp.contradicts} byIndex={byIndex} />
        </div>
      </div>
    </div>
  );
}

export function ResearchView({ question, onExit }: { question: string; onExit: () => void }) {
  const [plan, setPlan] = useState<SubQuestion[]>([]);
  const [steps, setSteps] = useState<ResearchStep[]>([]);
  const [report, setReport] = useState<ResearchReport | null>(null);
  const [status, setStatus] = useState<string>("running");
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setPlan([]);
    setSteps([]);
    setReport(null);
    setStatus("running");
    setError(null);
    deepResearch(
      question,
      {
        onPlan: (p) => setPlan(p.sub_questions),
        onProgress: (s) => setSteps((prev) => [...prev, s]),
        onReport: (r) => setReport(r),
        onDone: (d) => setStatus(d.status),
        onError: (e) => setError(e.message || "Research failed."),
      },
      { signal: ctrl.signal },
    ).catch((e) => {
      if ((e as Error).name === "AbortError") return;
      setError(e instanceof ApiError ? e.message : "Research failed.");
    });
    return () => ctrl.abort();
  }, [question]);

  const byIndex = new Map((report?.sources ?? []).map((s) => [s.index, s]));
  const coveredSubs = new Set(steps.filter((s) => s.reason === "plan").map((s) => s.sub_question_id));
  const g = report?.groundedness;

  return (
    <div className="research">
      <div className="research-head">
        <button className="back" onClick={onExit}>← Back</button>
        <span className={`status-pill ${status}`}>{status}</span>
      </div>
      <h2 className="research-q">{question}</h2>

      {error && <div className="error">{error}</div>}

      {!report && !error && (
        <div className="research-progress">
          {plan.length > 0 && (
            <ul className="sub-questions">
              {plan.map((sq) => (
                <li key={sq.id} className={coveredSubs.has(sq.id) ? "done" : ""}>
                  <span className="tick">{coveredSubs.has(sq.id) ? "✓" : "○"}</span>
                  {sq.question}
                </li>
              ))}
            </ul>
          )}
          <div className="steps">
            {steps.map((s) => (
              <div className="step" key={s.step}>
                <span className={`reason ${s.reason}`}>{s.reason}</span>
                <span className="s-query">{s.query}</span>
                <span className="s-claims">{s.claims} claims{s.disputed > 0 ? ` · ${s.disputed} disputed` : ""}</span>
              </div>
            ))}
            {plan.length === 0 && steps.length === 0 && <div className="thinking">Planning the research…</div>}
          </div>
        </div>
      )}

      {report && (
        <>
          {g && (
            <div className="groundedness">
              <span className="g-stat">
                <b>{g.findings_grounded}/{g.findings_total}</b> findings grounded
              </span>
              <span className="g-stat"><b>{g.well_supported}</b> well-supported</span>
              <span className={`g-badge ${g.answer_citations_valid ? "ok" : "warn"}`}>
                citations {g.answer_citations_valid ? "verified" : "unverified"}
              </span>
            </div>
          )}

          {report.executive_answer && (
            <Answer text={report.executive_answer} citations={asCitations(report.sources)} generator={report.generator} />
          )}

          {report.findings.length > 0 && (
            <>
              <div className="section-label">Findings <span className="count">{report.findings.length}</span></div>
              <div className="claims">
                {report.findings.map((f) => (
                  <FindingCard key={f.claim} f={f} byIndex={byIndex} />
                ))}
              </div>
            </>
          )}

          {report.disputed_points.length > 0 && (
            <>
              <div className="section-label">Where sources disagree <span className="count">{report.disputed_points.length}</span></div>
              <div className="claims">
                {report.disputed_points.map((dp) => (
                  <DisputedCard key={dp.claim} dp={dp} byIndex={byIndex} />
                ))}
              </div>
            </>
          )}

          {report.open_questions.length > 0 && (
            <>
              <div className="section-label">Open questions</div>
              <ul className="open-questions">
                {report.open_questions.map((q) => (
                  <li key={q}>{q}</li>
                ))}
              </ul>
            </>
          )}

          <div className="section-label">Sources <span className="count">{report.sources.length}</span></div>
          <SourceList sources={asSources(report.sources)} />
        </>
      )}
    </div>
  );
}
