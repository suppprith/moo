# Running moo in production

The trust basics an agent developer checks before routing real traffic: is it
up, how fast is it, what happens at the rate limit, and who gets told when it
breaks. Deploying the container itself is covered in [hosting](hosting.md).

## What the instance tells you

`GET /health` is the liveness probe. It needs no key, so a monitor never has to
hold one, and it stays out of the rate limiter.

```json
{ "status": "ok", "contract_version": "1.1", "auth": true,
  "uptime_seconds": 84321.4, "errors_5xx_recent": 0 }
```

`GET /metrics` is the detail: per-route p50, p95 and max latency over the last
500 requests, plus request and status-class counts.

```json
{ "uptime_seconds": 84321.4, "window": 500,
  "totals": { "requests": 12043, "2xx": 11890, "4xx": 152, "5xx": 1 },
  "errors_5xx_recent": 0, "error_window_seconds": 300.0,
  "routes": { "GET /search": { "requests": 9002, "p50_ms": 168.0,
                               "p95_ms": 402.0, "max_ms": 2211.0,
                               "2xx": 8999, "4xx": 3, "5xx": 0 } } }
```

Counters are per process and per rolling window, so several workers each report
their own share and a restart starts them over. That is the right trade for
something that must never grow without bound or store a query.

`/metrics` is gated when auth is on, since latency shape is operational
information. `/health` is not.

The published number to hold moo to is fast mode under 1s p50 warm. What that
means, and where the rest of the time goes, is in
[latency and cost](performance.md).

## Uptime monitoring

Point any monitor at `/health` on a one-minute interval and alert on a non-200
or on the body's `status` not being `ok`. UptimeRobot, BetterStack and Hetrix
all do this on a free tier, and any of them will host the public status page as
well. There is no moo-specific setup: the endpoint is a plain unauthenticated
GET that returns quickly and touches no models.

Two things worth alerting on beyond liveness:

- `errors_5xx_recent` from `/health` above zero for two consecutive checks. It
  counts 5xx responses in the last five minutes, which is the spike signal
  without needing a log pipeline.
- `p95_ms` on `GET /search` from `/metrics` above a second for five minutes,
  which usually means the model did not preload or the live-fetch budget is
  being spent in full on every call.

Both need the monitor to read a JSON field, which the paid tiers of the services
above do, and which a five-line cron script does for free.

## Rate limits

Every gated response carries what is left:

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 57
X-RateLimit-Reset: 41
```

`Reset` is seconds until the window frees a slot. A refused call returns 429
with the structured error envelope, `Retry-After`, and `X-RateLimit-Remaining:
0`. The limit is per key per process: `MOO_RATE_LIMIT_PER_MIN` sets the default,
an issued key can carry its own, and `MOO_DEMO_RATE_LIMIT_PER_MIN` meters
keyless callers on a public playground.

The headers are in the CORS allowlist, so a browser client can read them too.

## Errors

Every failure returns the same envelope with a stable `code` and the
`X-Request-ID` that also came back in the header, so a user report and a log
line can be joined without guessing:

```json
{ "error": { "code": "rate_limited", "message": "rate limit 60/min exceeded",
             "retryable": true, "request_id": "req_..." } }
```

## What is not built

Email alerting on a 5xx spike needs an SMTP or paging provider and an address to
send to, which is the operator's to choose. The instance exposes the count that
a rule fires on; wiring it to a mailbox is one monitor setting, not code that
belongs in the search engine.

There is no hosted status page yet because there is no hosted instance yet. When
there is one, the page is a monitor pointed at its `/health`, and it should
publish the p95 from `/metrics` next to the uptime figure rather than only the
green dot.
