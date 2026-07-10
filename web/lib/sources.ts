// Presentation helpers for sources: friendly labels, deterministic glyph
// colors, and trust tiers — so the UI reads like a product, not a data dump.

const LABELS: Record<string, string> = {
  docs: "Docs",
  blog: "Blog",
  github_issue: "GitHub Issue",
  github_pr: "GitHub PR",
  github_comment: "GitHub",
  github_release: "Release",
  so_answer: "Stack Overflow",
  so_question: "Stack Overflow",
  hn_story: "Hacker News",
  reddit_post: "Reddit",
};

const GLYPH_COLORS: Record<string, string> = {
  docs: "#2f7ae5",
  blog: "#8b5cf6",
  github_issue: "#57606a",
  github_pr: "#57606a",
  github_comment: "#57606a",
  github_release: "#1a7f37",
  so_answer: "#e8792b",
  so_question: "#e8792b",
  hn_story: "#ff6600",
  reddit_post: "#ff4500",
};

export function sourceLabel(type: string): string {
  return LABELS[type] ?? type.replace(/_/g, " ");
}

export function glyphColor(type: string): string {
  return GLYPH_COLORS[type] ?? "#6b7280";
}

export function glyphChar(type: string, host: string): string {
  if (type.startsWith("github")) return "GH";
  if (type.startsWith("so_")) return "SO";
  if (type === "hn_story") return "HN";
  if (type === "reddit_post") return "R";
  return (host.replace(/^www\./, "")[0] ?? "?").toUpperCase();
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export type TrustTier = "high" | "mid" | "low";

export function trustTier(score: number | null): TrustTier {
  if (score == null) return "low";
  if (score >= 0.66) return "high";
  if (score >= 0.4) return "mid";
  return "low";
}

export function trustColor(score: number | null): string {
  const t = trustTier(score);
  return t === "high"
    ? "var(--supports)"
    : t === "mid"
      ? "var(--explains)"
      : "var(--ink-3)";
}
