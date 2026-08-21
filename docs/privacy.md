# Privacy

Your queries are not stored. Not for training, not for analytics, not for
ranking, not for a "we may retain content to improve the service" clause. This
page says exactly what does get stored, so you can check it against the code
rather than trusting a promise.

One feature does feed ranking — the usefulness signal below — and it is off
unless you turn it on, keeps counters per source rather than per request, and
still never sees a query.

## Searches

A search leaves behind a counter and nothing else. Metering records, per key:
the number of requests, the route template they hit (`GET /search`, not the
query), the day, and the credits spent. That is the whole row, in
`api_key_usage`. The rate limiter keeps timestamps in memory and forgets them
after sixty seconds.

Logs are the same shape. A request logs its id, method, route template, status
and duration; the pipeline logs its stage timings and the mode it ran in. None
of them carry the query text.

`/metrics` reports p50 and p95 latency and status counts per route. It is
aggregate: no query, no key, no client address.

## Deep research

A research run is the exception, and it is deliberate. A resumable run has to
remember what it was researching, so `research_run` stores the question and the
sub-question queries the planner derived from it. That is what makes
`GET /research/{id}` able to hand you the report back later.

Delete the run and they go with it: `delete_run` removes the run, its steps and
its claim associations. The evidence itself stays in the shared graph, because
claims are not yours or anyone's, they are statements about public documents.

## Pages moo fetches

Search results come from fetching public web pages, and those pages are cached
in the store so a repeat query does not re-fetch them. That cache holds public
documents, not anything about you. Which pages get fetched follows from the
query, which is why the cache is worth understanding: on a shared instance, the
set of cached pages is a weak signal about what has been searched. On your own
instance, it is only ever your own.

## The usefulness signal

An agent can tell moo which of the sources it returned were actually cited
(`POST /v1/feedback`, or the `report_useful` MCP tool), so ranking learns which
pages are worth reading for software questions. This is **off by default** and
turns on with `MOO_FEEDBACK=1`.

What a signal writes is one counter per source:

| column | holds |
| --- | --- |
| `kind`, `target_id` | which chunk or document |
| `signal` | `cited`, `fetched`, `helpful`, `unhelpful` |
| `count` | how many times |
| `last_seen` | a **date**, not a timestamp |

What the table cannot hold, because the columns do not exist: the query or any
part or hash of it, the API key, a session or request id, an address, or a time
of day. There is no row per event, so it cannot be replayed as a sequence — it
says "this page was cited forty times", never "someone asked X then read Y".
Nothing about the caller reaches the module that writes it.

Turning it on is still a real choice, and here is the honest cost: on a
single-user instance, a count of 1 against an obscure page does say that you
read that page. Aggregate counters blur into anonymity with many users and not
with one. That is why the default is off rather than on, and why the counters
never leave your machine — nothing is uploaded, pooled across installs, or
shared.

`uv run python -m app.feedback` prints everything the store has learned, and
`--forget` deletes all of it. Ranking reads the table only while capture is on,
so a store that never opted in ranks exactly as it did before this existed.

## Accounts

Self-serve signup is off unless the operator configures GitHub OAuth. When it is
on, signing in stores one row: the provider, your GitHub id, your login, and
your public email if GitHub exposes it. No scopes are requested, and the OAuth
token is used for the one profile call and then dropped.

API keys are stored as a SHA-256 hash plus a short prefix for display. The key
itself is shown once, at issue time, and cannot be recovered from the database.

## Addresses

The public playground meters keyless callers per visitor, which needs something
to count against. It uses the client address as a bucket in memory only, and it
is never written to the database or the logs. With a key, the key is the bucket
and the address is not looked at.

## Self-hosting

Run it yourself and none of this leaves your machine. moo is one process and one
SQLite file, no telemetry, no phone-home, no vendor between you and the web. The
LLM provider is yours to choose, or to leave unset, in which case every stage
falls back to a deterministic path and nothing is sent anywhere.

## Changes

This file is versioned in the repo alongside the code it describes. If the
storage behaviour changes, this page changes in the same commit, and the history
is public.
