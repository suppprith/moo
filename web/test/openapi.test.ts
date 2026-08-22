import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { describeType, firstSentence, toReference } from "../lib/openapi.ts";

const SPEC = JSON.parse(
  readFileSync(path.join(process.cwd(), "..", "api", "openapi.json"), "utf8"),
);

test("an enum reads as its choices, not as 'string'", () => {
  assert.equal(
    describeType({ type: "string", enum: ["raw", "claims", "full"] }),
    "raw | claims | full",
  );
});

test("a bounded number carries its bounds", () => {
  assert.equal(describeType({ type: "integer", minimum: 1, maximum: 50 }), "integer 1-50");
});

test("an array names what it holds", () => {
  assert.equal(describeType({ type: "array", items: { type: "string" } }), "string[]");
});

test("an optional field is its type, not 'type or null'", () => {
  assert.equal(describeType({ anyOf: [{ type: "string" }, { type: "null" }] }), "string");
});

test("a missing schema is honest about being unknown", () => {
  assert.equal(describeType(undefined), "any");
});

test("the summary is one sentence, not the whole docstring", () => {
  const long =
    "Fetch a URL and return markdown.\n\nFailures are per-URL: each entry carries an error.";
  assert.equal(firstSentence(long), "Fetch a URL and return markdown.");
  assert.equal(firstSentence("No trailing period"), "No trailing period");
  assert.equal(firstSentence(undefined), "");
});

test("every committed endpoint lands in exactly one group", () => {
  const groups = toReference(SPEC);
  const paths = Object.entries(SPEC.paths).flatMap(([route, item]: [string, any]) =>
    Object.keys(item)
      .filter((method) => ["get", "post", "put", "patch", "delete"].includes(method))
      .map((method) => `${method.toUpperCase()} ${route}`),
  );
  const rendered = groups.flatMap((group) =>
    group.operations.map((operation) => `${operation.method} ${operation.path}`),
  );

  assert.equal(rendered.length, paths.length, "an operation went missing or got listed twice");
  assert.deepEqual(new Set(rendered), new Set(paths));
});

test("the drop-in endpoints are grouped where an agent author looks first", () => {
  const groups = toReference(SPEC);
  const dropIns = groups.find((group) => group.title === "Agent drop-ins");
  assert.ok(dropIns, "the drop-in group should exist");
  const paths = dropIns!.operations.map((operation) => operation.path);
  assert.ok(paths.includes("/v1/web_search"));
  assert.ok(paths.includes("/v1/extract"));
});

test("a POST body is flattened into fields with its required set", () => {
  const groups = toReference(SPEC);
  const extract = groups
    .flatMap((group) => group.operations)
    .find((operation) => operation.path === "/v1/extract" && operation.method === "POST");

  assert.ok(extract, "POST /v1/extract should be in the reference");
  const urls = extract!.fields.find((field) => field.name === "urls");
  assert.deepEqual(
    { required: urls?.required, type: urls?.type, location: urls?.location },
    { required: true, type: "string[]", location: "body" },
  );

  const depth = extract!.fields.find((field) => field.name === "depth");
  assert.equal(depth?.required, false);
  assert.equal(depth?.type, "raw | claims");
});

test("query and path parameters keep their location", () => {
  const groups = toReference(SPEC);
  const chunk = groups
    .flatMap((group) => group.operations)
    .find((operation) => operation.path === "/chunk/{id}");

  assert.ok(chunk);
  assert.equal(chunk!.fields.find((field) => field.name === "id")?.location, "path");
});

test("a spec with no paths renders no groups rather than empty headings", () => {
  assert.deepEqual(toReference({ paths: {} }), []);
  assert.deepEqual(toReference({}), []);
});
