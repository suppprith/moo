/**
 * Deciding which part of a source excerpt to mark.
 *
 * Two cases. When the API already picked spans out of the chunk (a search
 * result carries `highlights`), mark those exactly. When the panel was opened
 * from an evidence edge there is no span, only a claim, so mark the sentence
 * whose words overlap the claim most, and mark nothing when nothing is close
 * enough: a highlight in the wrong place is worse than none.
 */

const WORD = /[a-z0-9_]{3,}/g;

/** Below this, a "match" is coincidence rather than the sentence being cited. */
export const FOCUS_THRESHOLD = 0.34;
const MIN_SPAN_CHARS = 12;

export type HighlightInput = {
  spans?: string[];
  focus?: string;
};

export function words(text: string): Set<string> {
  return new Set(text.toLowerCase().match(WORD) ?? []);
}

/** Sentence ranges, kept as offsets so the text can be reassembled exactly. */
export function sentences(text: string): [number, number][] {
  const out: [number, number][] = [];
  const pattern = /[.!?]\s+|\n+/g;
  let start = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    const end = match.index + match[0].length;
    if (end - start > 1) out.push([start, end]);
    start = end;
  }
  if (start < text.length) out.push([start, text.length]);
  return out;
}

export function highlightRanges(text: string, input: HighlightInput): [number, number][] {
  const ranges: [number, number][] = [];
  for (const span of input.spans ?? []) {
    const trimmed = span.trim();
    if (trimmed.length < MIN_SPAN_CHARS) continue;
    const at = text.indexOf(trimmed);
    if (at !== -1) ranges.push([at, at + trimmed.length]);
  }
  if (ranges.length === 0 && input.focus) {
    const focus = words(input.focus);
    let best: { range: [number, number]; score: number } | null = null;
    for (const [start, end] of sentences(text)) {
      const overlap = [...words(text.slice(start, end))].filter((w) => focus.has(w)).length;
      const score = focus.size ? overlap / focus.size : 0;
      if (score > FOCUS_THRESHOLD && (!best || score > best.score)) {
        best = { range: [start, end], score };
      }
    }
    if (best) ranges.push(best.range);
  }
  return mergeOverlaps(ranges.sort((a, b) => a[0] - b[0]));
}

/** Overlapping spans would nest <mark> inside <mark>; join them instead. */
function mergeOverlaps(ranges: [number, number][]): [number, number][] {
  const out: [number, number][] = [];
  for (const [start, end] of ranges) {
    const last = out[out.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else out.push([start, end]);
  }
  return out;
}
