# moo as a SaaS: gap analysis vs Tavily, Firecrawl, Exa

*Written 2026-07-12. Companion to the Phase 14 live-crawl pivot and the competitive tickets (SUP-127..146). This doc is about the **product/business layer**, not the engine — the engine plan already exists in Linear.*

## Where moo actually stands

What's built (Phases 0–13): hybrid retrieval, evidence graph (claims / supports / contradicts / trust / confidence), deep-research loop with resumable runs and cited reports, MCP server (7 tools), drop-in `web_search` adapter, BYOK LLM, optional API-key auth, OpenAPI contract, benchmark harness, web UI. 258 tests, public repo, MIT.

What that is today: **an excellent self-hostable engine**. What it is not yet: **a product someone can adopt in 5 minutes without cloning a repo**. Tavily/Firecrawl/Exa win less on retrieval quality than on *adoption friction being near zero*: signup → key → `pip install` → working call in under 3 minutes, generous free tier, SDKs in every framework's docs.

## The honest competitive frame

| | Tavily | Firecrawl | Exa | moo (today) |
|---|---|---|---|---|
| Hosted API + signup | ✅ | ✅ | ✅ | ❌ local only |
| Free tier (credits) | ✅ 1k/mo | ✅ | ✅ | n/a |
| Python + JS SDKs | ✅ | ✅ | ✅ | ❌ raw HTTP/MCP |
| LangChain / LlamaIndex / Vercel AI integrations | ✅ | ✅ | ✅ | ❌ |
| URL → clean content (`/extract`, `/scrape`) | ✅ | ✅ core | ✅ `/contents` | ❌ internal only |
| Live web coverage | ✅ | ✅ | ✅ (own index) | ❌ until SUP-130 |
| Docs site + playground | ✅ | ✅ | ✅ | ❌ README only |
| Evidence graph, contradictions, confidence | ❌ | ❌ | ❌ | ✅ **unique** |
| Grounded deep research w/ resumable runs | partial | ❌ | partial | ✅ **unique** |
| Self-hostable / private / BYOK | ❌ | partial | ❌ | ✅ **unique** |

Conclusion: moo's differentiation is real (bottom three rows — nobody else has them). But differentiation only matters after **table-stakes adoption mechanics** exist. A better engine that requires cloning a repo loses to a worse engine with `pip install tavily-python`.

## Gap list (ranked by leverage)

### 1. No hosted service — the existential gap
Everything else is downstream of this. There is no URL an agent developer can hit. Needs: container image, deploy target (Fly.io/Railway/Hetzner VPS — cheap is fine at this stage), and a decision on multi-tenant storage (per-tenant SQLite files is actually a defensible v1; Postgres later). Auth exists (SUP-108) but keys are a comma-separated env var — no persistence, no self-serve issuance.

### 2. No `/extract` endpoint — cheapest parity win available
Firecrawl's entire business is "URL in, clean markdown out." moo already owns the whole pipeline internally (`Fetcher` + trafilatura + chunker + robots/ETag/rate-limit handling). Exposing `POST /v1/extract` (urls[] → markdown + metadata, optional `depth=claims` to run evidence over the pages) is days of work and makes moo a two-product API (search + extract) like Tavily. Also the natural building block for SUP-130 live fetch — build once, use twice.

### 3. No SDKs, no framework integrations — the distribution gap
Agent developers don't call HTTP APIs; they add a tool from their framework's docs. Every competitor is in LangChain/LlamaIndex/Vercel AI SDK/CrewAI integration pages — that's where discovery happens. Needs: `moo-python` and `moo-js` (thin, typed, ~300 lines each — OpenAPI spec already exists to generate from), then integration packages/PRs. The MCP server covers Claude-family agents well; these cover everyone else.

### 4. No self-serve onboarding — signup, keys, credits, dashboard
Free tier with metered credits is the standard growth motion (Tavily: 1k credits/mo free). Needs: minimal signup (email or GitHub OAuth), key issuance UI, persisted per-key quotas + usage (metering already exists in auth.py — it just isn't persisted or user-visible), Stripe for paid tiers. Pricing anchor: Tavily ~$0.008/credit, Exa $5/1k searches; moo's `deep` mode justifies premium pricing (it replaces a whole agent research loop, so compare cost-per-*answer*, not cost-per-search).

### 5. No docs site or playground
README + docs/*.md is fine for self-hosters, invisible to everyone else. Needs: hosted docs (Mintlify/Fumadocs — fast to stand up from existing markdown + OpenAPI) and a keyless playground (the web UI is 80% of this already — put a rate-limited demo instance behind it).

### 6. No positioning artifact
"Evidence-graph search engine" describes the tech, not the buyer's problem. The pitch that maps to a real pain: **agents confidently cite stale or contradicted information; moo is the search API that tells the agent when sources disagree and how confident to be.** One landing page + one comparison blog post ("we ran Claude with Tavily vs moo on 50 coding research tasks — here's where each failed") does more than any feature. Depends on SUP-129/141 benchmark numbers being real (which depends on SUP-128, the LLM key).

### 7. Ops/trust basics (needed at launch, not before)
Status page, uptime monitoring, structured request logging with the existing no-query-content privacy stance made into a written policy, rate-limit headers (partially done), SLO for fast mode (<1s p50 — SUP-146 covers the engineering side).

## What is deliberately NOT a gap

- **General-web coverage parity with Exa's index** — unwinnable solo; the live-fetch pivot (SUP-130) + CS/coding focus is the right counter.
- **A crawler fleet** — already correctly descoped in Phase 14.
- **Enterprise features** (SSO, VPC peering, SOC2) — years early. Self-hosting IS moo's enterprise story for now.
- **More engine features** — the engine is ahead of the product. Pausing engine work (except SUP-130/143, which make the demo real) to ship adoption mechanics is the right trade.

## Recommended sequence

1. **SUP-128** — set an LLM key (unblocks real evidence quality + honest benchmarks; 10 minutes).
2. **SUP-130 + SUP-143** — live fetch + on-demand evidence (the product story requires live web).
3. **`/extract` endpoint** — parity win, shared infra with #2.
4. **Hosted deployment + real key management** — the existential gap.
5. **SDKs (py/js) + docs site + playground** — adoption surface.
6. **SUP-131** (injection safety — required before hosting arbitrary live fetches for strangers) + **SUP-141/129** (benchmark) → launch post with head-to-head numbers.
7. Framework integrations + billing — after first external users, not before.

The one-sentence strategy: **reach zero-friction parity on adoption (hosted, SDK, extract, free tier), then win on the three rows of the table nobody else has.**
