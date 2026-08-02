/**
 * Contract-drift guard: the SDK must cover every endpoint moo publishes.
 * When the API grows an endpoint, this fails until the SDK maps it.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { ENDPOINTS, Moo } from '../src/index.ts';

const SPEC = resolve(dirname(fileURLToPath(import.meta.url)), '../../../api/openapi.json');
const HTTP_METHODS = new Set(['get', 'post', 'put', 'patch', 'delete']);

function publishedOperations(): string[] {
  const spec = JSON.parse(readFileSync(SPEC, 'utf8')) as {
    paths: Record<string, Record<string, unknown>>;
  };
  const operations: string[] = [];
  for (const [path, item] of Object.entries(spec.paths)) {
    for (const method of Object.keys(item)) {
      if (HTTP_METHODS.has(method.toLowerCase())) {
        operations.push(`${method.toUpperCase()} ${path}`);
      }
    }
  }
  return operations;
}

test('every published endpoint has a client method', () => {
  const missing = publishedOperations().filter((op) => !(op in ENDPOINTS));
  assert.deepEqual(missing, [], `SDK is missing methods for: ${missing.join(', ')}`);
});

test('no stale endpoints are mapped', () => {
  const published = new Set(publishedOperations());
  const stale = Object.keys(ENDPOINTS).filter((op) => !published.has(op));
  assert.deepEqual(stale, [], `SDK maps endpoints the API no longer publishes: ${stale.join(', ')}`);
});

test('mapped methods exist on the client', () => {
  for (const name of new Set(Object.values(ENDPOINTS))) {
    assert.equal(typeof (Moo.prototype as Record<string, unknown>)[name], 'function',
      `Moo.${name} is missing`);
  }
});
