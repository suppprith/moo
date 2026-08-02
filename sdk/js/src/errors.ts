/**
 * Typed errors mapped from moo's structured error envelope.
 *
 * Every API error arrives as `{error: {code, message, retryable, request_id}}`
 * with a stable `code`. Branch on `error.code`, retry only when
 * `error.retryable`, and quote `error.requestId` when reporting a failure.
 */

export type MooErrorCode =
  | 'invalid_request'
  | 'not_found'
  | 'unauthorized'
  | 'rate_limited'
  | 'budget_exceeded'
  | 'timeout'
  | 'upstream_error'
  | 'internal'
  | 'connection_error';

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    request_id: string;
  };
}

export interface MooErrorOptions {
  code?: MooErrorCode;
  retryable?: boolean;
  requestId?: string;
  status?: number;
  retryAfter?: number;
  body?: unknown;
}

export class MooError extends Error {
  readonly code: MooErrorCode;
  readonly retryable: boolean;
  readonly requestId?: string;
  readonly status?: number;
  readonly retryAfter?: number;
  readonly body?: unknown;

  constructor(message: string, options: MooErrorOptions = {}) {
    super(message);
    this.name = 'MooError';
    this.code = options.code ?? 'internal';
    this.retryable = options.retryable ?? false;
    this.requestId = options.requestId;
    this.status = options.status;
    this.retryAfter = options.retryAfter;
    this.body = options.body;
  }
}

const RETRYABLE_CODES = new Set<string>([
  'rate_limited',
  'timeout',
  'upstream_error',
  'internal',
  'connection_error',
]);

const STATUS_CODES: Record<number, MooErrorCode> = {
  401: 'unauthorized',
  403: 'unauthorized',
  404: 'not_found',
  422: 'invalid_request',
  429: 'rate_limited',
  500: 'internal',
  502: 'upstream_error',
  504: 'timeout',
};

function isEnvelope(body: unknown): body is ErrorEnvelope {
  return (
    typeof body === 'object' &&
    body !== null &&
    typeof (body as ErrorEnvelope).error === 'object' &&
    (body as ErrorEnvelope).error !== null
  );
}

/** Build a MooError from a response body, tolerating non-envelope bodies. */
export function fromEnvelope(
  body: unknown,
  status?: number,
  retryAfter?: number,
): MooError {
  const error = isEnvelope(body) ? body.error : undefined;
  const code = (error?.code ?? STATUS_CODES[status ?? 0] ?? 'internal') as MooErrorCode;
  return new MooError(error?.message ?? `request failed with status ${status ?? 'unknown'}`, {
    code,
    retryable: error?.retryable ?? RETRYABLE_CODES.has(code),
    requestId: error?.request_id,
    status,
    retryAfter,
    body,
  });
}

/** True when this failure is worth retrying as-is. */
export function isRetryable(error: unknown): boolean {
  return error instanceof MooError && error.retryable;
}
