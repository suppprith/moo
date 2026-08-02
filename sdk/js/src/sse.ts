/**
 * Server-Sent Events decoding.
 *
 * A frame ends at a blank line; `data:` lines accumulate. Frames whose data is
 * not JSON are surfaced as raw strings rather than dropped.
 */

export interface StreamEvent<T = unknown> {
  event: string;
  data: T;
}

function payload(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

export class SSEDecoder {
  private event = 'message';
  private data: string[] = [];

  feed(line: string): StreamEvent | undefined {
    const trimmed = line.endsWith('\r') ? line.slice(0, -1) : line;
    if (trimmed === '') return this.flush();
    if (trimmed.startsWith(':')) return undefined;
    const colon = trimmed.indexOf(':');
    const field = colon === -1 ? trimmed : trimmed.slice(0, colon);
    let value = colon === -1 ? '' : trimmed.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') this.event = value;
    else if (field === 'data') this.data.push(value);
    return undefined;
  }

  flush(): StreamEvent | undefined {
    if (this.data.length === 0) {
      this.event = 'message';
      return undefined;
    }
    const frame: StreamEvent = { event: this.event, data: payload(this.data.join('\n')) };
    this.event = 'message';
    this.data = [];
    return frame;
  }
}

/** Decode a byte stream into SSE events, buffering partial lines across chunks. */
export async function* decodeStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const utf8 = new TextDecoder();
  const decoder = new SSEDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += utf8.decode(value, { stream: true });
      let newline = buffer.indexOf('\n');
      while (newline !== -1) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        const frame = decoder.feed(line);
        if (frame) yield frame;
        newline = buffer.indexOf('\n');
      }
    }
    if (buffer) {
      const frame = decoder.feed(buffer);
      if (frame) yield frame;
    }
    const trailing = decoder.flush();
    if (trailing) yield trailing;
  } finally {
    reader.releaseLock();
  }
}
