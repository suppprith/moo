import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DocsSidebar } from "@/components/DocsSidebar";
import { SiteNav } from "@/components/SiteNav";
import { loadReference } from "@/lib/openapi";

export const metadata: Metadata = {
  title: "API reference | moo docs",
  description: "Every endpoint, generated from the committed OpenAPI spec.",
};

export default async function ApiReference() {
  const groups = await loadReference();
  if (!groups) notFound();

  return (
    <div className="site">
      <SiteNav current="docs" />
      <div className="docs-layout">
        <DocsSidebar current="api" />
        <main className="docs-main">
          <h1 className="page-title">API reference</h1>
          <p className="page-lede">
            Generated from{" "}
            <a href="https://github.com/suppprith/moo/blob/main/api/openapi.json">
              <code>api/openapi.json</code>
            </a>
            , the same committed spec the SDKs are generated from and a contract test asserts
            against — so this page cannot describe an endpoint the API does not have. Errors use one
            envelope with a stable <code>code</code>; handles are opaque (<code>chk_</code>,{" "}
            <code>doc_</code>, <code>clm_</code>, <code>ent_</code>).
          </p>

          {groups.map((group) => (
            <section className="api-group" key={group.title}>
              <h2 className="section-title">{group.title}</h2>
              <p className="api-group-blurb">{group.blurb}</p>

              {group.operations.map((operation) => (
                <article className="api-op" key={operation.id}>
                  <div className="api-op-head">
                    <span className={`api-method m-${operation.method.toLowerCase()}`}>
                      {operation.method}
                    </span>
                    <code className="api-path">{operation.path}</code>
                  </div>
                  {operation.summary && <p className="api-summary">{operation.summary}</p>}

                  {operation.fields.length > 0 && (
                    <div className="api-fields">
                      <table>
                        <thead>
                          <tr>
                            <th>Field</th>
                            <th>In</th>
                            <th>Type</th>
                            <th>Notes</th>
                          </tr>
                        </thead>
                        <tbody>
                          {operation.fields.map((field) => (
                            <tr key={`${operation.id}-${field.location}-${field.name}`}>
                              <td>
                                <code>{field.name}</code>
                                {field.required && <span className="api-req">required</span>}
                              </td>
                              <td className="api-in">{field.location}</td>
                              <td>
                                <code>{field.type}</code>
                              </td>
                              <td className="api-note">{field.description ?? ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </article>
              ))}
            </section>
          ))}

          <p className="docs-source">
            Source:{" "}
            <a href="https://github.com/suppprith/moo/blob/main/api/openapi.json">
              api/openapi.json
            </a>{" "}
            — regenerate with <code>uv run python -m app.export_openapi</code>.
          </p>
        </main>
      </div>
    </div>
  );
}
