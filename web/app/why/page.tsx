import type { Metadata } from "next";
import Link from "next/link";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Why moo",
  description:
    "Every search API gives an agent answers. None of them say when the sources disagree.",
};

type Row = { capability: string; moo: string; others: string; edge: "moo" | "them" | "even" };

const ROWS: Row[] = [
  {
    capability: "Live web retrieval",
    moo: "Yes, at query time",
    others: "Yes, all of them",
    edge: "even",
  },
  {
    capability: "Pages as clean markdown",
    moo: "Yes, with store handles",
    others: "Yes, all of them",
    edge: "even",
  },
  {
    capability: "Deep research with citations",
    moo: "Plan, multi-hop loop, cited report",
    others: "Tavily research, Exa deep",
    edge: "even",
  },
  {
    capability: "Says when sources disagree",
    moo: "Disputed claims with evidence on both sides",
    others: "No equivalent",
    edge: "moo",
  },
  {
    capability: "Version and staleness awareness",
    moo: "Supersedes edges, outdated sources flagged",
    others: "No equivalent",
    edge: "moo",
  },
  {
    capability: "Claim to source provenance",
    moo: "Every claim resolves to the chunk it came from",
    others: "Citations at page level",
    edge: "moo",
  },
  {
    capability: "Evidence that accumulates",
    moo: "The graph grows across queries",
    others: "Each call is independent",
    edge: "moo",
  },
  {
    capability: "Prompt-injection screening",
    moo: "Flagged, trust halved, flag travels to the caller",
    others: "Tavily screens; others vary",
    edge: "even",
  },
  {
    capability: "Self-host and bring your own key",
    moo: "One SQLite file, any LLM provider, no query logging",
    others: "Hosted only",
    edge: "moo",
  },
  {
    capability: "Index breadth and freshness",
    moo: "Scoped to software, no index of its own",
    others: "Web-scale crawls and indexes",
    edge: "them",
  },
  {
    capability: "Latency on a plain snippet search",
    moo: "Fetches at query time",
    others: "Served from a warm index",
    edge: "them",
  },
];

export default function WhyPage() {
  return (
    <div className="site">
      <SiteNav current="why" />
      <main className="site-main">
        <h1 className="page-title">Why moo</h1>
        <p className="claim">
          Every search API gives your agent answers. None of them tell it when the sources disagree,
          or when the top-ranked answer stopped being true. moo does, with the evidence for both
          sides.
        </p>

        <h2 className="section-title">The problem it is built for</h2>
        <p className="page-lede">
          The most-cited answer to a software question is often the oldest one. A flag was
          deprecated, a default changed, an API was removed, and the blog posts never caught up. An
          agent searching that corpus gets a confident, well-written, wrong answer, and passes it to
          you as fact. Retrieval quality does not fix this, because the stale answer really is the
          most relevant document.
        </p>
        <p className="page-lede">
          moo reads the pages it finds, extracts the claims they make, and links those claims to
          each other: this supports that, this contradicts that, this one supersedes an older one
          because it describes a newer version. What comes back is an answer that says which parts
          are contested and which are simply out of date.
        </p>

        <h2 className="section-title">Against the alternatives</h2>
        <div className="table-wrap">
          <table className="compare">
            <thead>
              <tr>
                <th>Capability</th>
                <th>moo</th>
                <th>Tavily, Exa, Firecrawl</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => (
                <tr key={row.capability} className={`edge-${row.edge}`}>
                  <td>{row.capability}</td>
                  <td>{row.moo}</td>
                  <td>{row.others}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="page-note">
          The last two rows are theirs, honestly. They crawl the open web at a scale one person
          cannot match, and a warm index answers faster than a live fetch. moo is not trying to beat
          them at coverage. It is scoped to software questions, where being current and being
          contradiction-aware matters more than having everything.
        </p>

        <h2 className="section-title">What that looks like in practice</h2>
        <div className="why-grid">
          <div className="why-card">
            <h3>For an agent</h3>
            <p>
              Point an existing <code>web_search</code> tool at moo and the result rows are the
              familiar shape, with trust scores and handles alongside. Ask for depth and you get
              claims, confidence, and contradictions in the same payload.
            </p>
            <Link href="/docs/agents">Agent guide</Link>
          </div>
          <div className="why-card">
            <h3>For a person</h3>
            <p>
              The playground runs the same engine. Search, switch to claims, and see which ones are
              disputed and which sources back each side. Deep research streams its plan as it goes.
            </p>
            <Link href="/">Try it</Link>
          </div>
          <div className="why-card">
            <h3>For your own hardware</h3>
            <p>
              One SQLite file, local embeddings, any LLM provider or none, no query logging, no
              per-query bill. The hosted instance runs the same code path.
            </p>
            <Link href="/docs/hosting">Hosting</Link>
          </div>
        </div>

        <h2 className="section-title">How the claim gets tested</h2>
        <p className="page-lede">
          A pitch nobody checks is marketing. moo ships a versioned set of stale-answer traps: real
          queries whose popular answer is out of date, each with the answer that is current and the
          release that changed it. An engine passes a trap only by surfacing the current answer and
          marking the old one. The gate fails if moo cannot clear it, or if a competitor keeps up,
          because then this page would need rewriting.
        </p>
      </main>
    </div>
  );
}
