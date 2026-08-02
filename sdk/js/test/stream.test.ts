import assert from 'node:assert/strict';
import test from 'node:test';

import { Moo, MooError, SSEDecoder } from '../src/index.ts';

const FRAMES =
  'event: progress\ndata: {"stage": "searching"}\n\n' +
  'event: source\ndata: {"id": "chk_1"}\n\n' +
  'event: done\ndata: {"mode": "raw"}\n\n';

function streaming(bodyText: string, status = 200) {
  return async () =>
    new Response(bodyText, { status, headers: { 'content-type': 'text/event-stream' } });
}

test('the decoder joins multiline data and ignores comments', () => {
  const decoder = new SSEDecoder();
  const frames = [];
  for (const line of ['event: plan', 'data: {"a": 1}', '', ': comment', 'event: done',
    'data: {"b":', 'data: 2}', '']) {
    const frame = decoder.feed(line);
    if (frame) frames.push(frame);
  }
  assert.deepEqual(frames, [
    { event: 'plan', data: { a: 1 } },
    { event: 'done', data: { b: 2 } },
  ]);
});

test('searchStream yields events in order', async () => {
  const client = new Moo({ baseUrl: 'https://moo.test', fetch: streaming(FRAMES) });
  const seen: string[] = [];
  for await (const frame of client.searchStream('wal mode')) seen.push(frame.event);
  assert.deepEqual(seen, ['progress', 'source', 'done']);
});

test('an error event raises a typed error mid-stream', async () => {
  const body =
    'event: progress\ndata: {"stage": "planning"}\n\n' +
    'event: error\ndata: {"error": {"code": "internal", "message": "research failed", ' +
    '"retryable": true, "request_id": "rid"}}\n\n';
  const client = new Moo({ baseUrl: 'https://moo.test', fetch: streaming(body) });
  const seen: string[] = [];
  const error = await (async () => {
    try {
      for await (const frame of client.researchStream('why')) seen.push(frame.event);
    } catch (e) {
      return e as MooError;
    }
    return undefined;
  })();
  assert.deepEqual(seen, ['progress']);
  assert.equal(error?.code, 'internal');
  assert.equal(error?.requestId, 'rid');
});

test('a failure before the first byte raises the http error', async () => {
  const body = JSON.stringify({
    error: { code: 'unauthorized', message: 'no key', retryable: false, request_id: 'r' },
  });
  const client = new Moo({
    baseUrl: 'https://moo.test',
    maxRetries: 0,
    fetch: streaming(body, 401),
  });
  const error = await (async () => {
    try {
      for await (const _ of client.searchStream('q')) void _;
    } catch (e) {
      return e as MooError;
    }
    return undefined;
  })();
  assert.equal(error?.code, 'unauthorized');
});

test('frames split across chunks are reassembled', async () => {
  const chunks = ['event: pro', 'gress\ndata: {"stage": "sea', 'rching"}\n\nevent: done\ndata: {}\n\n'];
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  const client = new Moo({
    baseUrl: 'https://moo.test',
    fetch: async () =>
      new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } }),
  });
  const seen: Array<{ event: string; data: unknown }> = [];
  for await (const frame of client.searchStream('q')) seen.push(frame);
  assert.deepEqual(seen, [
    { event: 'progress', data: { stage: 'searching' } },
    { event: 'done', data: {} },
  ]);
});
