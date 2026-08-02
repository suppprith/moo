# Hosting moo

moo is built to run on your own hardware, and a hosted instance is the same
process with a volume attached and keys turned on. This page is the container
image, the deploy, and the storage decision behind both.

## Run the image

```bash
docker compose up --build      # from the repo root
curl localhost:8000/health
```

Or directly:

```bash
docker build -t moo ./api
docker run -p 8000:8000 -v moo-data:/data moo
```

The image bakes the embedding weights at build time and sets `MOO_PRELOAD=1`, so
a container warms the model on boot rather than making the first search wait for
it. The store lives at `MOO_DB_PATH=/data/moo.sqlite` on a mounted volume; the
image itself is stateless and can be replaced without losing the evidence graph
or the page cache.

It is a big image. `sentence-transformers` pulls in torch, which is the price of
running embeddings locally instead of paying an embedding API per call. If image
size matters more to you than build simplicity, install a CPU-only torch wheel
(`--index-url https://download.pytorch.org/whl/cpu`) before `uv sync`; it cuts
the largest layer by roughly a gigabyte.

## Deploy

`api/fly.toml` is a working starting point: one machine, one volume, HTTPS,
health checks against `/health`, and suspend-when-idle so an instance nobody is
calling costs nothing.

```bash
cd api
fly launch --no-deploy --copy-config
fly volumes create moo_data --size 3 --region <region>
fly secrets set MOO_LLM_API_KEY=... MOO_BRAVE_API_KEY=...
fly deploy
```

Railway, Render, or a plain VPS with `docker compose up -d` behind a reverse
proxy work the same way; the only requirements are a persistent volume and about
2 GB of memory for the embedding model. Nothing in moo assumes Fly.

Set `MOO_CORS_ORIGINS` to the origins your web UI is served from. Leave it empty
for an agent-only instance: agents do not need CORS, and browsers are the only
thing it affects.

## The storage decision

**One SQLite file per instance, and per tenant when there are tenants. Postgres
only when concurrency actually demands it.**

Since live retrieval landed, the store is a cache with an evidence graph on top,
not a corpus. Pages arrive at query time, live under a TTL per source tier, and
get evicted when they age out (never while their chunks back a claim). What
accumulates is the part worth keeping: claims, evidence edges, contradictions,
trust, and the supersedes graph.

That changes what the database has to be good at. There is no shared corpus to
keep consistent across instances, so the strongest argument for a central
database is gone. What is left favours SQLite:

- **Isolation for free.** A tenant is a file. Deleting a tenant is deleting a
  file, backing one up is copying it, and one tenant's write load cannot touch
  another's.
- **It matches the self-host story.** The hosted instance runs the same code
  path as someone running moo on a laptop, so there is no second storage backend
  to keep working.
- **The write pattern fits.** WAL mode gives concurrent readers with a single
  writer, and moo's writes are bursts of page ingestion, not sustained OLTP.
- **Vector search is already there.** `sqlite-vec` handles the index in the same
  file, so there is no separate vector service to run, back up, or pay for.

The honest limits, and the point at which this stops being right:

- **One writer per file.** Heavy concurrent ingestion into one tenant will
  serialize. Fine at one instance; the reason not to run many workers against
  one file.
- **The file is on a volume, so the machine is stateful.** Horizontal scaling
  means more instances with their own caches, warming independently, not a pool
  behind one database.
- **Around 500k to 1M vectors**, brute-force similarity stops being free and an
  ANN index (HNSW, or an external vector store) starts paying for itself. That
  threshold is a corpus-scale problem, and a cache with an eviction bound does
  not naturally get there.

Move to Postgres when a single tenant needs concurrent writers, or when several
instances must share one evidence graph. Neither is true yet, and building for
it now would cost the isolation and the self-host parity that make the current
shape good.

## API keys

Two ways to gate an instance. Both accept `Authorization: Bearer <key>` or
`X-API-Key`, and `/health`, `/contract` and `/v1/tools` stay open either way.

**Fixed keys**, unchanged and fine for a private deploy:

```bash
MOO_API_KEYS=key1,key2 MOO_RATE_LIMIT_PER_MIN=60
```

**Issued keys**, which is what a hosted instance wants:

```bash
docker compose exec api python -m app.keys create --label alice --quota 1000
docker compose exec api python -m app.keys list
docker compose exec api python -m app.keys revoke moo_sk_a1b2c3
```

A key is shown once and stored only as a sha256 hash, so a leaked database does
not leak usable keys. Each carries an optional per-key rate limit and request
quota, and counters that survive restarts. A quota that runs out returns
`budget_exceeded` (429, not retryable) rather than `rate_limited` (429,
retryable), so a client can tell "come back in a minute" from "you are out".

Once an instance has issued a key it stays gated: revoking the last key does not
silently reopen it. Delete the rows to go back to open.

`GET /usage` reports both views: `usage` is this process's masked in-memory
tally, `keys` is the persisted per-key counters. Neither ever includes a key or
any query content.

Rate-limit windows are per process. Several workers each get the configured
limit, so divide it accordingly or run one worker per machine, which is the
default here anyway.

## Self-serve signup and credits

Off unless you configure it, because an open key-minting endpoint is an abuse
vector and a self-hosted moo needs no signup at all. With
`MOO_GITHUB_CLIENT_ID`, `MOO_GITHUB_CLIENT_SECRET` and `MOO_PUBLIC_URL` set,
`/signup` becomes the whole flow: sign in with GitHub, get a key shown once, call
the API. GitHub is identity only, with no scopes requested, the account id, login
and public email stored, and no token kept after the exchange. Without those
variables, `/signup` says how to issue a key by hand instead.

Calls are weighted rather than counted, because a deep-research run is not a
snippet search:

| Call | Credits |
| --- | --- |
| `/search`, `/v1/web_search` | 1 |
| `/v1/extract` | 1 per URL |
| `/research` | 10, plus 5 per step past the default, capped at 25 |
| `/source`, `/chunk`, `/claim`, `/graph`, re-reading a run | 0 |

Drill-down being free is deliberate. Charging to follow a citation would tax the
one thing that makes moo worth using.

New keys get `MOO_FREE_CREDITS` (default 1000) per rolling 30-day window, reset
lazily on the first call after the window closes, so there is no scheduler. A key
with no allowance is unmetered, which is what `python -m app.keys create` issues
and what self-hosting wants. Running out returns `budget_exceeded` with the reset
date; a 5xx is never billed.

`/account` is a dashboard: paste a key, see credits and a per-endpoint
breakdown. The key is held in the tab, never stored and never put in a URL. The
same data is available as JSON at `GET /account/usage` and through both SDKs as
`account_usage()`.

Paid tiers are a mailto for now. Building billing before there are users would
be the wrong order.

## A public playground

The web app doubles as the try-it-now surface, and a demo instance has to survive
being linked. `MOO_DEMO_RATE_LIMIT_PER_MIN` meters keyless callers per visitor
(by `X-Forwarded-For`, falling back to the socket address, used as a bucket and
never stored) while keyed callers keep their own quota. Unset, keyless calls are
unlimited, which is right on a laptop and wrong on the internet.

On the web side, `NEXT_PUBLIC_DEMO_MODE=1` tells visitors they are on a shared
instance and points them at signup or self-hosting. Set `MOO_CORS_ORIGINS` to
the site's origin, or the browser will not be allowed to call the API at all.

## Operational notes

- **Cold start** is dominated by loading the embedding model, a few seconds from
  a warm page cache. `MOO_PRELOAD=1` moves it to boot, and the health check's
  60-second grace period covers it. Suspend-when-idle machines pay it again on
  wake.
- **Backups** are a file copy. `sqlite3 /data/moo.sqlite ".backup /tmp/moo.bak"`
  is consistent under WAL; copying the file directly while writes are in flight
  is not.
- **Cache growth**: set `MOO_CACHE_MAX_DOCS` to bound the live-page cache.
  Eviction is oldest-first over live documents only, and never touches a
  document whose chunks back a claim.
- **What is not covered here**: signup, billing, multi-region, and a status
  page. Those are separate work, and none of them are needed for a stranger with
  a key to call `POST /v1/web_search` successfully.
