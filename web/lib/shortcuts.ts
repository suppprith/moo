/**
 * Keyboard-first navigation: the decisions, separated from the wiring.
 *
 * Everything here is a pure function so the awkward parts (do not steal a key
 * while someone is typing; where does j wrap to) can be tested without a
 * browser, which is also where they usually break.
 */

export type Command = {
  id: string;
  label: string;
  hint?: string;
  keys?: string;
  group: "Search" | "Go" | "View";
};

const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** True when a keystroke belongs to whatever the user is typing in. */
export function isTypingTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element || !element.tagName) return false;
  if (TYPING_TAGS.has(element.tagName)) return true;
  return element.isContentEditable === true;
}

/** True for the modifier combinations a page must never intercept. */
export function isSystemChord(event: {
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  key: string;
}): boolean {
  if (event.altKey) return true;
  if (!event.ctrlKey && !event.metaKey) return false;
  return event.key.toLowerCase() !== "k";
}

/** Move a selection without wrapping: at the end, stay at the end. Wrapping
 *  loses your place in a long result list more often than it helps. */
export function moveSelection(current: number, delta: number, length: number): number {
  if (length === 0) return -1;
  if (current < 0) return delta > 0 ? 0 : length - 1;
  return Math.min(length - 1, Math.max(0, current + delta));
}

/** Subsequence match, so "dr" finds "Deep research" without a fuzzy library. */
export function matchesQuery(text: string, query: string): boolean {
  const haystack = text.toLowerCase();
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  if (haystack.includes(needle)) return true;
  let at = 0;
  for (const char of needle) {
    if (char === " ") continue;
    at = haystack.indexOf(char, at);
    if (at === -1) return false;
    at += 1;
  }
  return true;
}

export function filterCommands(commands: Command[], query: string): Command[] {
  return commands.filter((command) => matchesQuery(`${command.label} ${command.hint ?? ""}`, query));
}

export const SHORTCUTS: { keys: string; does: string }[] = [
  { keys: "/", does: "focus the search box" },
  { keys: "Ctrl K", does: "command palette" },
  { keys: "j / k", does: "move down and up the results" },
  { keys: "Enter", does: "open the selected source" },
  { keys: "o", does: "open the selected source in a new tab" },
  { keys: "1 2 3", does: "raw, claims, full" },
  { keys: "d", does: "deep research this query" },
  { keys: "g", does: "toggle the evidence graph" },
  { keys: "?", does: "this list" },
  { keys: "Esc", does: "close whatever is open" },
];
