import { promises as fs } from "fs";
import path from "path";

/**
 * The docs site reads the same markdown the repo ships, so there is one copy of
 * every explanation. A page that drifts from the code is a page nobody trusts,
 * and keeping the site's source in `docs/` means the two cannot separate.
 */

export type DocPage = {
  slug: string;
  title: string;
  blurb: string;
  file: string;
  group: "Start here" | "Reference" | "Operate";
};

export const DOC_PAGES: DocPage[] = [
  {
    slug: "agents",
    title: "Agent guide",
    blurb: "Every tool, what it returns, and the workflow an agent follows through them.",
    file: "agents.md",
    group: "Start here",
  },
  {
    slug: "integrations",
    title: "Framework integrations",
    blurb: "LangChain, LlamaIndex, the Vercel AI SDK, CrewAI, and raw tool loops.",
    file: "integrations.md",
    group: "Start here",
  },
  {
    slug: "agent-demo",
    title: "Recorded demo",
    blurb: "A real run: deep research, then chasing one claim back to its source.",
    file: "agent-demo.md",
    group: "Start here",
  },
  {
    slug: "data-model",
    title: "Data model and contract",
    blurb: "Documents, chunks, claims, evidence, handles, errors, and streaming.",
    file: "data-model.md",
    group: "Reference",
  },
  {
    slug: "scope",
    title: "What moo covers",
    blurb: "The software domain moo is scoped to, and the gold queries behind it.",
    file: "v1-vertical.md",
    group: "Reference",
  },
  {
    slug: "hosting",
    title: "Hosting",
    blurb: "Container, deploy, keys and credits, and the storage decision.",
    file: "hosting.md",
    group: "Operate",
  },
  {
    slug: "performance",
    title: "Latency and cost",
    blurb: "Per-stage timings, the budgets, and what a cold query spends on models.",
    file: "performance.md",
    group: "Operate",
  },
  {
    slug: "gaps",
    title: "Where moo stands",
    blurb: "The honest gap analysis against Tavily, Exa and Firecrawl.",
    file: "saas-gap-analysis.md",
    group: "Operate",
  },
];

export const DOC_GROUPS = ["Start here", "Reference", "Operate"] as const;

const REPO = "https://github.com/suppprith/moo/blob/main";

/** The build may run from web/ or from the repo root, depending on the host. */
const DOCS_DIRS = [
  path.join(process.cwd(), "..", "docs"),
  path.join(process.cwd(), "docs"),
];

export function findDoc(slug: string): DocPage | undefined {
  return DOC_PAGES.find((page) => page.slug === slug);
}

/**
 * Rewrite repo-relative links so they still work on the site: a sibling doc
 * becomes a site route, anything else points back at GitHub rather than 404ing.
 */
function rewriteLinks(markdown: string): string {
  return markdown.replace(/\]\((?!https?:|#)([^)]+)\)/g, (match, target: string) => {
    const clean = target.replace(/^\.\//, "");
    const sibling = DOC_PAGES.find((page) => clean === page.file || clean.endsWith("/" + page.file));
    if (sibling) return `](/docs/${sibling.slug})`;
    const repoPath = clean.replace(/^\.\.\//, "");
    return `](${REPO}/${repoPath})`;
  });
}

/** Drop the leading H1: the page renders its own title. */
function stripTitle(markdown: string): string {
  return markdown.replace(/^#\s+.*\n+/, "");
}

export async function loadDoc(slug: string): Promise<string | null> {
  const page = findDoc(slug);
  if (!page) return null;
  for (const dir of DOCS_DIRS) {
    try {
      const raw = await fs.readFile(path.join(dir, page.file), "utf8");
      return rewriteLinks(stripTitle(raw));
    } catch {
      continue;
    }
  }
  return null;
}
