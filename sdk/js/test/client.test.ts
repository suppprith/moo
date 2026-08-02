import assert from 'node:assert/strict';
import test from 'node:test';

import { Moo } from '../src/index.ts';

function recorder(payload: unknown = { ok: true }, status = 200) {
  const seen: { url?: string; method?: string; headers?: Headers; body?: unknown } = {};
  const fetchImpl = async (url: string, init?: RequestInit) => {
    seen.url = url;
    seen.method = init?.method;
    seen.headers = new Headers(init?.headers);
    seen.body = init?.body ? JSON.parse(String(init.body)) : undefined;
    return new Response(JSON.stringify(payload), {
      status,
      headers: { 'content-type': 'application/json' },
    });
  };
  return { fetchImpl, seen };
}

test('defaults to the local server', () => {
  const previous = process.env.MOO_BASE_URL;
  delete process.env.MOO_BASE_URL;
  assert.equal(new Moo().baseUrl, 'http://127.0.0.1:8000');
  if (previous !== undefined) process.env.MOO_BASE_URL = previous;
});

test('reads configuration from the environment', () => {
  process.env.MOO_BASE_URL = 'https://api.example.com/';
  process.env.MOO_API_KEY = 'sk-env';
  const client = new Moo();
  assert.equal(client.baseUrl, 'https://api.example.com');
  assert.equal(client.apiKey, 'sk-env');
  delete process.env.MOO_BASE_URL;
  delete process.env.MOO_API_KEY;
});

test('sends the api key as a bearer token', async () => {
  const { fetchImpl, seen } = recorder();
  await new Moo({ apiKey: 'sk-test', baseUrl: 'https://moo.test', fetch: fetchImpl }).health();
  assert.equal(seen.headers?.get('authorization'), 'Bearer sk-test');
});

test('search sends only the options that were set', async () => {
  const { fetchImpl, seen } = recorder({ query: 'wal', sources: [] });
  const client = new Moo({ baseUrl: 'https://moo.test', fetch: fetchImpl });
  await client.search('wal mode', { mode: 'claims', k: 5, live: false });
  assert.equal(seen.url, 'https://moo.test/search?q=wal+mode&mode=claims&k=5&live=false');
});

test('webSearch posts the request body', async () => {
  const { fetchImpl, seen } = recorder({ query: 'x', results: [] });
  const client = new Moo({ baseUrl: 'https://moo.test', fetch: fetchImpl });
  await client.webSearch('redis eviction', { maxResults: 3, depth: 'claims' });
  assert.equal(seen.method, 'POST');
  assert.equal(seen.url, 'https://moo.test/v1/web_search');
  assert.deepEqual(seen.body, { query: 'redis eviction', max_results: 3, depth: 'claims' });
});

test('research maps camelCase options onto the contract', async () => {
  const { fetchImpl, seen } = recorder({ question: 'why' });
  const client = new Moo({ baseUrl: 'https://moo.test', fetch: fetchImpl });
  const outputSchema = { type: 'object', properties: { answer: { type: 'string' } } };
  await client.research('why', { maxSteps: 3, maxSeconds: 20, outputSchema });
  assert.deepEqual(seen.body, {
    question: 'why',
    max_steps: 3,
    max_seconds: 20,
    output_schema: outputSchema,
  });
});

test('extract posts the url list', async () => {
  const { fetchImpl, seen } = recorder({ results: [] });
  const client = new Moo({ baseUrl: 'https://moo.test', fetch: fetchImpl });
  await client.extract(['https://a.dev'], { depth: 'claims' });
  assert.deepEqual(seen.body, { urls: ['https://a.dev'], depth: 'claims' });
});

test('handle endpoints build encoded paths', async () => {
  const { fetchImpl, seen } = recorder({ id: 'chk_7' });
  const client = new Moo({ baseUrl: 'https://moo.test', fetch: fetchImpl });
  await client.chunk('chk_7');
  assert.equal(seen.url, 'https://moo.test/chunk/chk_7');
  await client.source('doc_2');
  assert.equal(seen.url, 'https://moo.test/source/doc_2');
  await client.claim('clm_9');
  assert.equal(seen.url, 'https://moo.test/claim/clm_9');
  await client.getResearch('run/1');
  assert.equal(seen.url, 'https://moo.test/research/run%2F1');
});

test('graph passes depth and cap', async () => {
  const { fetchImpl, seen } = recorder({ nodes: [] });
  const client = new Moo({ baseUrl: 'https://moo.test', fetch: fetchImpl });
  await client.graph('postgres vacuum', { depth: 2, cap: 30 });
  assert.equal(seen.url, 'https://moo.test/graph?q=postgres+vacuum&depth=2&cap=30');
});
