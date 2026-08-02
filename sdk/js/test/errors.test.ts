import assert from 'node:assert/strict';
import test from 'node:test';

import { Moo, MooError, isRetryable } from '../src/index.ts';

function envelope(code: string, message: string, retryable = false) {
  return { error: { code, message, retryable, request_id: 'rid-1' } };
}

function jsonResponse(body: unknown, status: number, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });
}

test('not found maps to a typed error carrying the envelope', async () => {
  const client = new Moo({
    baseUrl: 'https://moo.test',
    fetch: async () => jsonResponse(envelope('not_found', "no chunk 'chk_9'"), 404),
  });
  const error = await client.chunk('chk_9').then(
    () => undefined,
    (e: unknown) => e as MooError,
  );
  assert.ok(error instanceof MooError);
  assert.equal(error.code, 'not_found');
  assert.equal(error.requestId, 'rid-1');
  assert.equal(error.status, 404);
  assert.equal(error.retryable, false);
  assert.equal(isRetryable(error), false);
});

test('a non-envelope body falls back to the status', async () => {
  const client = new Moo({
    maxRetries: 0,
    baseUrl: 'https://moo.test',
    fetch: async () => new Response('<html>bad gateway</html>', { status: 502 }),
  });
  const error = await client.health().then(
    () => undefined,
    (e: unknown) => e as MooError,
  );
  assert.equal(error?.code, 'upstream_error');
  assert.equal(error?.retryable, true);
});

test('rate limits are retried honoring Retry-After', async () => {
  let calls = 0;
  const delays: Array<[number, number | undefined]> = [];
  const client = new Moo({
    baseUrl: 'https://moo.test',
    retryDelay: (attempt, retryAfter) => {
      delays.push([attempt, retryAfter]);
      return 0;
    },
    fetch: async () => {
      calls += 1;
      if (calls === 1) {
        return jsonResponse(envelope('rate_limited', 'slow down', true), 429, {
          'retry-after': '3',
        });
      }
      return jsonResponse({ status: 'ok' }, 200);
    },
  });
  assert.deepEqual(await client.health(), { status: 'ok' });
  assert.equal(calls, 2);
  assert.deepEqual(delays, [[0, 3]]);
});

test('retries are bounded then the error surfaces', async () => {
  let calls = 0;
  const client = new Moo({
    baseUrl: 'https://moo.test',
    maxRetries: 2,
    retryDelay: () => 0,
    fetch: async () => {
      calls += 1;
      return jsonResponse(envelope('rate_limited', 'slow down', true), 429, {
        'retry-after': '1',
      });
    },
  });
  const error = await client.health().then(
    () => undefined,
    (e: unknown) => e as MooError,
  );
  assert.equal(calls, 3);
  assert.equal(error?.code, 'rate_limited');
  assert.equal(error?.retryAfter, 1);
});

test('client errors are not retried', async () => {
  let calls = 0;
  const client = new Moo({
    baseUrl: 'https://moo.test',
    retryDelay: () => 0,
    fetch: async () => {
      calls += 1;
      return jsonResponse(envelope('invalid_request', 'mode: bad value'), 422);
    },
  });
  await assert.rejects(() => client.search('q', { mode: 'nope' as never }));
  assert.equal(calls, 1);
});

test('an unreachable server raises a retryable connection error', async () => {
  const client = new Moo({
    baseUrl: 'https://moo.test',
    maxRetries: 1,
    retryDelay: () => 0,
    fetch: async () => {
      throw new TypeError('fetch failed');
    },
  });
  const error = await client.health().then(
    () => undefined,
    (e: unknown) => e as MooError,
  );
  assert.equal(error?.code, 'connection_error');
  assert.equal(error?.retryable, true);
});
