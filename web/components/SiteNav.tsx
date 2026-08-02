import Link from "next/link";

/** Header for the pages that are not the search UI: docs, why, and anything
 *  else that needs a way back to the playground. */
export function SiteNav({ current }: { current?: "docs" | "why" }) {
  return (
    <header className="site-header">
      <div className="site-header-inner">
        <Link href="/" className="site-wordmark">
          moo
        </Link>
        <nav className="site-links">
          <Link href="/">Playground</Link>
          <Link href="/docs" className={current === "docs" ? "current" : undefined}>
            Docs
          </Link>
          <Link href="/why" className={current === "why" ? "current" : undefined}>
            Why moo
          </Link>
          <a href="https://github.com/suppprith/moo">GitHub</a>
        </nav>
      </div>
    </header>
  );
}
