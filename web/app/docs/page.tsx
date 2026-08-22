import type { Metadata } from "next";
import Link from "next/link";
import { SiteNav } from "@/components/SiteNav";
import { DOC_GROUPS, DOC_PAGES } from "@/lib/docs";

export const metadata: Metadata = {
  title: "Docs | moo",
  description: "Agent guide, data model, integrations and hosting for moo.",
};

const QUICKSTARTS = [
  {
    label: "MCP",
    body: "claude mcp add moo -- uv run --directory /path/to/moo/api python -m app.mcp_server",
  },
  { label: "Python", body: 'pip install moo\nMoo().web_search("postgres connection pooling")' },
  { label: "JavaScript", body: 'npm install moo-js\nawait new Moo().webSearch("wal mode")' },
  {
    label: "HTTP",
    body: "curl -s $MOO/v1/web_search -H 'content-type: application/json' \\\n  -d '{\"query\":\"why is my postgres query slow\"}'",
  },
];

export default function DocsIndex() {
  return (
    <div className="site">
      <SiteNav current="docs" />
      <main className="site-main">
        <h1 className="page-title">Docs</h1>
        <p className="page-lede">
          moo is search infrastructure for AI agents: live retrieval over software sources, an
          evidence layer that says where sources disagree, and deep research that cites everything
          it claims.
        </p>

        <h2 className="section-title">Four ways in</h2>
        <div className="quickstarts">
          {QUICKSTARTS.map((entry) => (
            <div className="quickstart" key={entry.label}>
              <div className="quickstart-label">{entry.label}</div>
              <pre>{entry.body}</pre>
            </div>
          ))}
        </div>

        {DOC_GROUPS.map((group) => {
          const pages = DOC_PAGES.filter((page) => page.group === group);
          if (!pages.length) return null;
          return (
            <section key={group}>
              <h2 className="section-title">{group}</h2>
              <div className="doc-cards">
                {pages.map((page) => (
                  <Link className="doc-card" href={`/docs/${page.slug}`} key={page.slug}>
                    <span className="doc-card-title">{page.title}</span>
                    <span className="doc-card-blurb">{page.blurb}</span>
                  </Link>
                ))}
              </div>
            </section>
          );
        })}

        <h2 className="section-title">API reference</h2>
        <p className="page-lede">
          <Link href="/docs/api">Every endpoint</Link>, generated from the committed OpenAPI spec,
          so it cannot describe an API that does not exist. A running instance also serves the spec
          at <code>/openapi.json</code>, an interactive reference at <code>/docs</code>, and the
          machine-readable descriptor (contract version, handle formats, error taxonomy, MCP tool
          schemas) at <code>/contract</code>.
        </p>
      </main>
    </div>
  );
}
