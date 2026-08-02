/** Rendering moo payloads into what a model should actually read. */

const MAX_SNIPPET = 500;

export interface WebSearchPayload {
  results?: Array<Record<string, any>>;
  notice?: string;
  [key: string]: unknown;
}

export interface ResearchPayload {
  executive_answer?: string;
  disputed_points?: Array<{ text?: string; supports?: number[]; contradicts?: number[] }>;
  open_questions?: string[];
  sources?: Array<{ n?: number; url?: string; title?: string | null }>;
  status?: string;
  [key: string]: unknown;
}

export function renderResults(payload: WebSearchPayload): string {
  const rows = payload.results ?? [];
  if (rows.length === 0) return 'No results.';
  const lines: string[] = [];
  rows.forEach((row, index) => {
    let header = `[${index + 1}] ${row.title ?? row.url}`;
    if (typeof row.trust_score === 'number') header += ` (trust ${row.trust_score.toFixed(2)})`;
    if (row.suspicious) header += ' (flagged: page contains prompt-injection patterns)';
    lines.push(header, String(row.url ?? ''));
    if (row.snippet) lines.push(String(row.snippet).slice(0, MAX_SNIPPET));
    lines.push('');
  });
  if (payload.notice) lines.push(payload.notice);
  return lines.join('\n').trim();
}

export function renderPages(payload: WebSearchPayload): string {
  const parts: string[] = [];
  for (const page of payload.results ?? []) {
    if (page.error) {
      parts.push(`# ${page.url}\nfailed: ${page.error.message}`);
      continue;
    }
    parts.push(`# ${page.title ?? page.url}\n${page.url}\n\n${page.markdown ?? ''}`);
  }
  if (payload.notice) parts.push(payload.notice);
  return parts.join('\n\n').trim() || 'No pages were read.';
}

export function renderReport(report: ResearchPayload): string {
  const lines: string[] = [
    report.executive_answer ?? 'No answer could be grounded in sources.',
  ];

  const disputed = report.disputed_points ?? [];
  if (disputed.length > 0) {
    lines.push('\nWhere sources disagree:');
    for (const point of disputed) {
      const supports = (point.supports ?? []).map((n) => `[S${n}]`).join(', ');
      const contradicts = (point.contradicts ?? []).map((n) => `[S${n}]`).join(', ');
      lines.push(
        `- ${point.text} (supported by ${supports || 'none'}, contradicted by ${
          contradicts || 'none'
        })`,
      );
    }
  }

  const open = report.open_questions ?? [];
  if (open.length > 0) {
    lines.push('\nStill open:');
    for (const question of open) lines.push(`- ${question}`);
  }

  const sources = report.sources ?? [];
  if (sources.length > 0) {
    lines.push('\nSources:');
    for (const source of sources) {
      lines.push(`[S${source.n}] ${source.title ?? source.url} - ${source.url}`);
    }
  }

  if (report.status === 'partial') {
    lines.push('\nThe run hit its budget, so this report is partial.');
  }
  return lines.join('\n').trim();
}
