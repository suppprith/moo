import Link from "next/link";
import { DOC_GROUPS, DOC_PAGES } from "@/lib/docs";

/**
 * One sidebar for every docs page, markdown or not. The API reference is a
 * generated page rather than a file in `docs/`, and it still has to sit in the
 * same list — a reader should not have to know which pages are markdown.
 */
export function DocsSidebar({ current }: { current?: string }) {
  return (
    <aside className="docs-sidebar">
      {DOC_GROUPS.map((group) => (
        <div className="docs-group" key={group}>
          <div className="docs-group-title">{group}</div>
          {DOC_PAGES.filter((entry) => entry.group === group).map((entry) => (
            <Link
              className={`docs-link${entry.slug === current ? " current" : ""}`}
              href={`/docs/${entry.slug}`}
              key={entry.slug}
            >
              {entry.title}
            </Link>
          ))}
          {group === "Reference" && (
            <Link className={`docs-link${current === "api" ? " current" : ""}`} href="/docs/api">
              API reference
            </Link>
          )}
        </div>
      ))}
    </aside>
  );
}
