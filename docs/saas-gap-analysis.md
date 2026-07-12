# moo as a SaaS: gap analysis vs Tavily, Firecrawl, Exa

*Written 2026-07-12; competitor facts verified against live docs/pricing pages the same day (sources at bottom). Companion to the Phase 14 live-crawl pivot and the competitive tickets (SUP-127..146). This doc is about the **product/business layer**, not the engine — the engine plan already exists in Linear.*

## Where moo actually stands

What's built (Phases 0–13): hybrid retrieval, evidence graph (claims / supports / contradicts / trust / confidence), deep-research loop with resumable runs and cited reports, MCP server (7 tools), drop-in `web_search` adapter, BYOK LLM, optional API-key auth, OpenAPI contract, benchmark harness, web UI. 258 tests, public repo, MIT.

What that is today: **an excellent self-hostable engine**. What it is not yet: **a product someone can adopt in 5 minutes without cloning a repo**. Tavily/Firecrawl/Exa win less on retrieval quality than on *adoption friction being near zero*: signup → key → `pip install` → working call in under 3 minutes, generous free tier, SDKs in every framework's docs.

## The honest competitive frame (verified 2026-07-12)

What each actually ships today:

- **Tavily**: `/search` (depths ultra-fast→advanced; topic general/news/finance; time ranges; domain filters; `auto_parameters`), `/extract`, `/crawl`, `/map`, **`/research`** (mini/pro models, SSE streaming, custom `output_schema`, citation formats — a full deep-research endpoint, not "partial"), `/usage`. Free 1,000 credits/mo; PAYG $0.008/credit; basic search = 1 credit, advanced = 2.
- **Firecrawl**: Scrape (1 credit/page), Crawl, Map, **Search** (2 credits/10 results — they compete in search too, not just scraping), Interact (browser automation, 2 credits/min), **Monitor** (page-change tracking), Agent (preview). Free 1,000 credits; Hobby $16/mo (5k credits) → Scale $599/mo (1M).
- **Exa**: six search modes (instant/fast/auto/deep-lite/deep/deep-reasoning), `/contents` ($1/1k pages), highlights + summaries, **structured `outputSchema` with field-level grounding, citations, and low/medium/high confidence levels**, SSE streaming, Monitors, Websets, Agent, MCP server. Free tier up to 20k requests/mo; search $7/1k; deep search $12–15/1k.

| | Tavily | Firecrawl | Exa | moo (today) |
|---|---|---|---|---|
| Hosted API + signup | ✅ | ✅ | ✅ | ❌ local only |
| Free tier | ✅ 1k credits/mo | ✅ 1k credits | ✅ ~20k req/mo | n/a |
| Python + JS SDKs + framework listings | ✅ | ✅ | ✅ | ❌ raw HTTP/MCP |
| URL → clean content | ✅ `/extract` | ✅ core | ✅ `/contents` | ❌ internal only |
| Crawl / map a site | ✅ | ✅ core | subpages | ❌ |
| Live web coverage | ✅ | ✅ | ✅ (own index) | ❌ until SUP-130 |
| Deep research endpoint w/ cited report | ✅ `/research` | ❌ (Agent preview) | ✅ deep modes | ✅ |
| Structured output schema for research | ✅ | ✅ (extract) | ✅ | ❌ |
| Confidence signals on output | ❌ | ❌ | ✅ field-level low/med/high | ✅ per-claim numeric |
| Change monitoring | ❌ | ✅ Monitor | ✅ Monitors | ❌ |
| **Contradiction detection / disputed claims (two-sided evidence)** | ❌ | ❌ | ❌ | ✅ **unique** |
| **Persistent evidence graph accumulating across queries** | ❌ | ❌ | ❌ | ✅ **unique** |
| **Claim-level provenance handles (drill from any claim to exact source chunk)** | ❌ | ❌ | ❌ | ✅ **unique** |
| Self-hostable / private / BYOK | ❌ | partial (OSS repo) | ❌ | ✅ **unique** |
| Resumable research runs (poll/resume by run_id) | ❌ | ❌ | ❌ | ✅ |

Two sober corrections this research forced:
1. **"Deep research" alone is not a moat.** Tavily `/research` and Exa deep/deep-reasoning both ship cited, streamed, schema-structured research. moo's deep_research is table stakes in that company — what's differentiated is *what's inside it*: contradictions surfaced as first-class output, per-claim confidence with inspectable evidence edges, and runs that persist/resume.
2. **Exa already sells "confidence."** Field-level low/med/high grounding confidence. moo's version is stronger (numeric, per-claim, derived from an inspectable support/contradict evidence graph rather than an opaque label) — but the pitch can't pretend confidence itself is novel. The novel word is **disagreement**: nobody tells the agent when sources contradict each other.

Conclusion stands, sharpened: differentiation is real but narrower than the engine plan assumed, and it only matters after **table-stakes adoption mechanics** exist. A better engine that requires cloning a repo loses to a worse engine with `pip install tavily-python`.

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
"Evidence-graph search engine" describes the tech, not the buyer's problem. The buyer's problem (per the project's founding thesis): **AI agents confidently use and repeat stale data.** They cite the old API, the deprecated flag, the answer that was true two major versions ago. The pitch fuses that with what the research verified as unique:

> **AI agents keep shipping stale answers. moo crawls the live web at query time and tells your agent what's current, what's been superseded, and where sources disagree — with the evidence for both sides.**

Why this framing wins: "fresh results" alone is table stakes (Tavily/Exa are live too — their freshness means *recently fetched pages*). moo's claim is stronger: staleness is treated as **disagreement with the present** — the evidence graph detects when a newer, higher-trust source supersedes an older claim (SUP-136 version/recency-aware answers, SUP-137 supersedes edges) instead of just returning both and letting the agent pick wrong. Live fetch (SUP-130) makes it current; the evidence layer makes it *confidently* current. Confidence alone can't headline — Exa ships confidence labels — but "current + contradiction-checked" is uncontested. One landing page + one comparison blog post ("we ran Claude with Tavily vs moo on 50 coding research tasks — here's where each failed") does more than any feature. Depends on SUP-129/141 benchmark numbers being real (which depends on SUP-128, the LLM key). Note the eval must benchmark against Tavily `/research` and Exa deep mode — their real research products — not just their basic search.

### 6b. Feature gaps surfaced by the research (candidates, not yet critical)
- **Structured `output_schema` on deep_research** — all three competitors let callers define the JSON shape of synthesized output. moo returns a fixed report shape; agents increasingly expect schema-in. Cheap to add on top of the existing report assembler.
- **Site crawl/map endpoint** — Tavily and Firecrawl both ship it; natural extension of `/extract` (SUP-147) once the fetch path exists. Defer until extract has users.
- **Monitors / change tracking** — Exa and Firecrawl both sell this. For moo it's actually a *strong* fit later: "watch this claim — alert me when new evidence contradicts it" is a monitor product nobody else can build. Post-launch.

### 7. Ops/trust basics (needed at launch, not before)
Status page, uptime monitoring, structured request logging with the existing no-query-content privacy stance made into a written policy, rate-limit headers (partially done), SLO for fast mode (<1s p50 — SUP-146 covers the engineering side).

## What is deliberately NOT a gap

- **General-web coverage parity with Exa's index** — unwinnable solo; the live-fetch pivot (SUP-130) + CS/coding focus is the right counter.
- **A crawler fleet** — already correctly descoped in Phase 14.
- **Enterprise features** (SSO, VPC peering, SOC2) — years early. Self-hosting IS moo's enterprise story for now.
- **More engine features** — the engine is ahead of the product. Pausing engine work (except SUP-130/143, which make the demo real) to ship adoption mechanics is the right trade.

## The USP, made enforceable (2026-07-12)

The USP is one falsifiable capability: **given a query whose popular answer is stale or contradicted, moo returns the current answer, marks the old one superseded/disputed, and shows evidence for both sides — competitors return the stale answer with no warning.**

In Linear this is now protected structurally: the `usp` label marks the tickets that carry it (SUP-143 on-demand evidence, SUP-136 version-aware answers, SUP-137 supersedes edges, SUP-138 corroboration ranking, SUP-144 accumulating evidence cache), and **SUP-156 is the acceptance gate** — a stale-trap benchmark (10–20 real queries with verifiably outdated popular answers, run against moo + Tavily /search+/research + Exa auto+deep) that must pass before SUP-153 can launch. If a scope cut ever threatens a `usp` ticket, the cut is wrong.

## Recommended sequence

1. **SUP-128** — set an LLM key (unblocks real evidence quality + honest benchmarks; 10 minutes).
2. **SUP-130 + SUP-143** — live fetch + on-demand evidence (the product story requires live web).
3. **`/extract` endpoint** — parity win, shared infra with #2.
4. **Hosted deployment + real key management** — the existential gap.
5. **SDKs (py/js) + docs site + playground** — adoption surface.
6. **SUP-131** (injection safety — required before hosting arbitrary live fetches for strangers) + **SUP-141/129** (benchmark) → launch post with head-to-head numbers.
7. Framework integrations + billing — after first external users, not before.

The one-sentence strategy: **reach zero-friction parity on adoption (hosted, SDK, extract, free tier), then win on the rows of the table nobody else has — led by contradiction detection.**

## Sources (verified 2026-07-12)

- Tavily search API reference: https://docs.tavily.com/documentation/api-reference/endpoint/search (endpoints incl. /research, /crawl, /map; credit costs)
- Tavily /research reference: https://docs.tavily.com/documentation/api-reference/endpoint/research (models, streaming, output_schema, citation formats)
- Tavily pricing: https://www.tavily.com/pricing (1k credits/mo free, $0.008/credit PAYG)
- Firecrawl pricing: https://www.firecrawl.dev/pricing (products + tiers + credit costs)
- Exa pricing: https://exa.ai/pricing (free ~20k req/mo, $7/1k search, $12–15/1k deep, monitors/agent/websets)
- Exa search/research API reference: https://exa.ai/docs/reference/research/create-a-task (six modes, outputSchema, field-level grounding + confidence, SSE)
