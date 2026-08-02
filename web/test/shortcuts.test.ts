import assert from "node:assert/strict";
import test from "node:test";

import {
  SHORTCUTS,
  filterCommands,
  isSystemChord,
  isTypingTarget,
  matchesQuery,
  moveSelection,
} from "../lib/shortcuts.ts";
import type { Command } from "../lib/shortcuts.ts";

const COMMANDS: Command[] = [
  { id: "research", label: "Deep research this query", group: "Search" },
  { id: "mode:raw", label: "Raw results", hint: "no LLM calls", group: "View" },
  { id: "docs", label: "Docs", group: "Go" },
];

function element(tagName: string, contentEditable = false) {
  return { tagName, isContentEditable: contentEditable } as unknown as EventTarget;
}

test("keys typed into an input are never stolen", () => {
  assert.equal(isTypingTarget(element("INPUT")), true);
  assert.equal(isTypingTarget(element("TEXTAREA")), true);
  assert.equal(isTypingTarget(element("SELECT")), true);
  assert.equal(isTypingTarget(element("DIV", true)), true);
});

test("keys pressed on the page are ours", () => {
  assert.equal(isTypingTarget(element("DIV")), false);
  assert.equal(isTypingTarget(element("BODY")), false);
  assert.equal(isTypingTarget(null), false);
});

test("browser chords are left alone, except ctrl+k", () => {
  const chord = (over: Partial<KeyboardEvent>) =>
    ({ ctrlKey: false, metaKey: false, altKey: false, key: "j", ...over }) as KeyboardEvent;
  assert.equal(isSystemChord(chord({ ctrlKey: true, key: "r" })), true);
  assert.equal(isSystemChord(chord({ metaKey: true, key: "c" })), true);
  assert.equal(isSystemChord(chord({ altKey: true })), true);
  assert.equal(isSystemChord(chord({ ctrlKey: true, key: "k" })), false);
  assert.equal(isSystemChord(chord({})), false);
});

test("selection starts at the right end depending on direction", () => {
  assert.equal(moveSelection(-1, 1, 5), 0);
  assert.equal(moveSelection(-1, -1, 5), 4);
});

test("selection clamps instead of wrapping", () => {
  assert.equal(moveSelection(4, 1, 5), 4, "at the end, stay put");
  assert.equal(moveSelection(0, -1, 5), 0, "at the top, stay put");
  assert.equal(moveSelection(2, 1, 5), 3);
});

test("an empty list has nothing to select", () => {
  assert.equal(moveSelection(0, 1, 0), -1);
});

test("a query matches by substring or by subsequence", () => {
  assert.equal(matchesQuery("Deep research this query", "research"), true);
  assert.equal(matchesQuery("Deep research this query", "dr"), true);
  assert.equal(matchesQuery("Deep research this query", "drq"), true);
  assert.equal(matchesQuery("Deep research this query", "zz"), false);
  assert.equal(matchesQuery("anything", ""), true);
});

test("filtering searches the hint as well as the label", () => {
  assert.deepEqual(
    filterCommands(COMMANDS, "no llm").map((c) => c.id),
    ["mode:raw"],
  );
  assert.equal(filterCommands(COMMANDS, "").length, 3);
});

test("the help list stays in step with what is bound", () => {
  const keys = SHORTCUTS.map((s) => s.keys);
  for (const expected of ["/", "j / k", "Enter", "?", "Esc"]) {
    assert.ok(keys.includes(expected), `${expected} is missing from the help`);
  }
});
