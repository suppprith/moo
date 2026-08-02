import assert from "node:assert/strict";
import test from "node:test";

import { highlightRanges, sentences } from "../lib/highlight.ts";

const TEXT =
  "WAL mode is enabled with a pragma. In WAL mode readers do not block the writer " +
  "and the writer does not block readers. Rollback journal mode is the older default.";

function marked(text: string, ranges: [number, number][]): string[] {
  return ranges.map(([start, end]) => text.slice(start, end).trim());
}

test("an api span is marked exactly where it appears", () => {
  const ranges = highlightRanges(TEXT, { spans: ["readers do not block the writer"] });
  assert.equal(ranges.length, 1);
  assert.deepEqual(marked(TEXT, ranges), ["readers do not block the writer"]);
});

test("a span the chunk does not contain is skipped, not guessed at", () => {
  assert.deepEqual(highlightRanges(TEXT, { spans: ["something never written here"] }), []);
});

test("very short spans are ignored as coincidence", () => {
  assert.deepEqual(highlightRanges(TEXT, { spans: ["WAL"] }), []);
});

test("a claim highlights the sentence it came from", () => {
  const ranges = highlightRanges(TEXT, {
    focus: "in WAL mode readers do not block the writer",
  });
  assert.equal(ranges.length, 1);
  assert.match(marked(TEXT, ranges)[0]!, /^In WAL mode readers do not block/);
});

test("an unrelated claim highlights nothing rather than the wrong sentence", () => {
  assert.deepEqual(
    highlightRanges(TEXT, { focus: "kubernetes pods restart when memory is exhausted" }),
    [],
  );
});

test("spans win over the claim heuristic when both are given", () => {
  const ranges = highlightRanges(TEXT, {
    spans: ["Rollback journal mode is the older default"],
    focus: "readers do not block the writer",
  });
  assert.deepEqual(marked(TEXT, ranges), ["Rollback journal mode is the older default"]);
});

test("overlapping spans merge instead of nesting marks", () => {
  const ranges = highlightRanges(TEXT, {
    spans: ["readers do not block the writer", "do not block the writer and the writer"],
  });
  assert.equal(ranges.length, 1);
  assert.match(marked(TEXT, ranges)[0]!, /readers do not block the writer and the writer/);
});

test("ranges come back in document order", () => {
  const ranges = highlightRanges(TEXT, {
    spans: ["Rollback journal mode is the older default", "WAL mode is enabled with a pragma"],
  });
  assert.equal(ranges.length, 2);
  assert.ok(ranges[0]![0] < ranges[1]![0]);
});

test("nothing to go on means nothing marked", () => {
  assert.deepEqual(highlightRanges(TEXT, {}), []);
  assert.deepEqual(highlightRanges("", { focus: "anything" }), []);
});

test("sentence offsets reassemble the original text", () => {
  const parts = sentences(TEXT).map(([start, end]) => TEXT.slice(start, end));
  assert.equal(parts.join(""), TEXT);
});

test("newlines split sentences too, so a list item can be highlighted", () => {
  const text = "Options:\nUse WAL mode for readers\nUse a rollback journal otherwise";
  const ranges = highlightRanges(text, { focus: "use WAL mode for readers" });
  assert.deepEqual(marked(text, ranges), ["Use WAL mode for readers"]);
});
