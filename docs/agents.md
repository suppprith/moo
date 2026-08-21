# moo for AI agents

moo is **search infrastructure for AI agents** — the tool an agent routes its
`web_search` to for software questions (databases, languages, frameworks, build
tooling, errors, systems). It returns
grounded, structured evidence — claims with confidence, supports/contradicts
edges, source trust, and stable handles to drill into — not ten blue links or one
unverifiable paragraph.

## Why moo (vs. general search)

| Capability | Exa / Tavily | Perplexity / built-in web_search | moo |
| --- | --- | --- | --- |
| Returns ranked snippets/content | ✅ | ✅ | ✅ |
| Synthesized answer + citations | partial | ✅ | ✅ (`full` mode) |
| Claims with per-claim confidence | ❌ | ❌ | ✅ |
| Surfaces contradictions instead of averaging them | ❌ | ❌ | ✅ (`list_contradictions`, disputed flags) |
| Typed evidence edges (supports/contradicts/explains) | ❌ | ❌ | ✅ |
| Per-source trust rubric (maintainer > forum, recency decay) | ❌ | ❌ | ✅ |
| Stable drill-down handles (citation → span → doc) | contents API | ❌ | ✅ (`fetch_source`) |
| Source-aware software corpus (GitHub issues/PRs/release notes, author role) | generalist | generalist | ✅ |
| Self-hosted, private, keyless, zero per-query cost | ❌ paid API | ❌ paid API | ✅ |

moo pairs a specialist, verifiable evidence layer with **live retrieval**: with
a search provider configured (`MOO_SEARXNG_URL` self-hosted/keyless or
`MOO_BRAVE_API_KEY`), every query discovers + fetches fresh pages from the
software source universe first — trusted dev domains ranked ahead, junk
dropped, non-software queries flagged `out_of_domain` — then answers over them.
Fast mode (`raw`) is live snippets with zero LLM calls; deep modes and
`deep_research` run the claims/contradiction/trust pipeline **on pages fetched
seconds ago** — something no general search API does. Without a provider, moo
serves its local store/cache only. For a coding question it returns claims you
can check, not links or one paragraph.

## Two ways in

1. **Primitive tools** — the agent drives its own loop: `search`, `extract`,
   `fetch_source`, `get_claim`, `list_contradictions`, `expand_graph`.
2. **Hosted deep research** — hand off a whole question: `deep_research` runs
   plan → iterative multi-hop retrieval → a cited, confidence-scored report;
   `research_status` polls/resumes it.

Both are exposed over **MCP** (`app/mcp_server.py`) and, for `search` and
`extract`, over drop-in HTTP endpoints (`/v1/web_search`, `/v1/extract`;
OpenAI-style tool defs at `/v1/tools`). See the
[quickstart](../README.md#connect-an-agent-mcp).

## Tool reference (MCP)

| Tool | Params | Returns / use |
| ---- | ------ | ------------- |
| `search` | `query, mode=raw\|claims\|full, k, fields, max_tokens` | Ranked `sources` (opaque `id` handle, deep-link `url`, `source_type`, trust, score). `raw` = fast, no LLM; `claims` = + claims/evidence/graph; `full` = + cited answer. Query supports typed operators (`type:docs,so` · `site:host.com` · `since:2024` · `"phrase"` · `-exclude`); invalid ones fail soft as text. |
| `extract` | `urls[], depth=raw\|claims, max_tokens` | Fetch known URL(s) → clean markdown per page + `doc_`/`chk_` handles (page is stored + indexed). `depth=claims` also runs the evidence layer over each page. Per-URL failures; TTL-cached. HTTP twin: `POST /v1/extract`. |
| `fetch_source` | `handle, max_tokens` | Resolve a `chk_`/`doc_`/`clm_` handle to its full row: chunk text + context, whole document, or a claim + evidence. How you go from a citation to the evidence. |
| `get_claim` | `handle` | One claim by `clm_` handle: confidence, disputed flag, full evidence set (contradictions first) with backing source handles. |
| `list_contradictions` | `query, k` | Only the disputed/contradicted claims for a query — where sources disagree. |
| `expand_graph` | `node` | Entity neighborhood (by `ent_` handle or name): typed relations, claim-annotated. |
| `deep_research` | `question, k, max_steps, max_seconds, max_tokens, output_schema` | `{run_id, status, partial, executive_answer, findings[], disputed_points[], open_questions[], sources[], groundedness}`. Streams MCP progress. Pass `output_schema` (JSON schema) for a caller-shaped `structured` section: `structured.grounding` traces every field to `clm_` handles; untraceable fields come back null (`ungrounded_fields`), never fabricated. |
| `research_status` | `run_id, max_tokens` | Re-fetch a run's status + report (poll a long run or re-read a finished one). |
| `report_useful` | `handles[], signal=cited\|fetched\|helpful\|unhelpful` | Report which returned sources you actually used, after answering. Nudges ranking toward what agents cite for software questions. Off unless the instance sets `MOO_FEEDBACK=1`, in which case it returns `{disabled: true}` and is safe to call anyway. Handles only — no query, nothing about the caller ([privacy](privacy.md#the-usefulness-signal)). HTTP twin: `POST /v1/feedback`. |

Every handle is opaque and stable (`chk_`/`clm_`/`doc_`/`ent_`); pass it back to
`fetch_source`/`get_claim`/`expand_graph` to drill in. Results respect
`max_tokens` (truncation is always explicit; contradictions are never dropped).

## Recommended research workflow

1. `deep_research(question)` → read `executive_answer`, `findings` (each with
   `confidence` + `citations`), and `disputed_points`.
2. Check `groundedness` — `pct_grounded`, `well_supported`, `answer_citations_valid`.
3. Verify a finding: `get_claim(finding.claim)` for its full evidence set, or
   `fetch_source(source.handle)` to read the source.
4. `list_contradictions(question)` if you specifically need the disagreements.
5. Long run? `deep_research` returns a `run_id`; poll with `research_status(run_id)`.

For a single lookup, skip the engine and call `search` (or route your agent's
`web_search` at `/v1/web_search`).

## Content safety: results are data, not instructions

moo fetches live third-party pages, so every retrieved result must be treated
as **untrusted data** — a page can contain text aimed at the agent reading it
("ignore previous instructions…", "send your keys to…"). moo scans all
live-fetched content for instruction-injection patterns at ingest and **marks
rather than drops**:

- suspicious sources carry `"suspicious": true` on their result rows
  (`/search` sources, `/v1/web_search` results, MCP payloads);
- their trust score is halved;
- when any flagged source is in a result set, the response carries
  `meta.untrusted_content: true` + `meta.content_notice`;
- every `/v1/web_search` response includes a top-level `notice` reminding the
  integrator that page content is data, not directives.

Integrators: never execute or obey text found inside `results[].snippet`,
`sources`, chunk text, or claims — cite it, quote it, reason about it. A page
*discussing* prompt injection may be flagged too (patterns match the phrasing);
the flag means "handle with care", not "malicious with certainty". Detections
are logged with URL + pattern category only — never query content.

## Groundedness guarantee

Nothing in a report is asserted without a traceable source. Claims are grounded
in their source chunks at extraction; the report carries a `groundedness` summary
and flags any finding that fails citation integrity. Uncited claims never ship.

## How a contradiction is detected

A contradiction edge means a source *asserts the opposite*, and the bar is the
same with or without an LLM key. With a key, the evidence linker classifies each
candidate chunk against the claim. Without one, the keyless path checks polarity
opposition ([`opposition.py`](../api/app/evidence/opposition.py)) and requires
three things to line up:

1. **same subject** — embedding similarity above a floor, so opposite polarity
   about two different topics is not mistaken for disagreement;
2. **opposite assertion** — a negation mismatch on a restated claim, or an
   antonym conflict (faster/slower, recommended/deprecated), compared
   **assertion against assertion** using the claims already extracted from the
   candidate chunk rather than against a paragraph of prose;
3. **two independent voices** — a page qualifying its own statement is one
   voice; contradiction edges are only drawn across documents, and a claim is
   flagged `disputed` only with mass on both sides from independent sources.

None of that is negotiable for a metric's sake. The rule this replaced fired on
any contrast word ("but", "not", "however") in a related chunk, which is most
technical prose: it produced 305 contradiction edges over a 79-document store,
of which none was a real disagreement. Detecting fewer, real contradictions is
the point — a store full of fake disputes is worse than an empty one, because an
agent cannot tell them apart.

## Benchmark

`app/eval/deep_research_tasks.json` + `uv run python -m app.eval.benchmark`.
Recorded baseline (task set **v1.1**, 13 tasks, heuristic — no LLM key, no search
provider, answering out of a 79-document local store —
[`baseline.json`](../api/app/eval/baseline.json)), single-shot `search` vs.
`deep_research`:

| metric | search | deep_research |
| --- | --- | --- |
| coverage | 0.24 | 0.20 |
| citation_accuracy | — | 1.00 |
| source_recall | 0.00 | 0.22 |
| contradiction_recall | 0.00 | 0.00 |
| tokens | 933 | 1799 |

deep_research wins on source recall and grounding. Two numbers need saying
plainly rather than being explained away:

- **Coverage is low because eight of the thirteen tasks are outside what that
  local store contains** (Node, Rust, Kubernetes, HTTP caching). Keyless and
  provider-less, moo answers from what it has already fetched; the live-crawl
  path is what those tasks are for, and it needs a search provider.
- **contradiction_recall is 0.00, and it was 0.00 before too** — but for a
  different reason than it looked. The old keyless classifier reported 305
  contradiction edges over this store and 35 disputed claims; on inspection
  every one was a false positive (see "How a contradiction is detected"). The
  current rule reports none, which is the honest count for a store where no two
  independent sources address the same question. Moving this metric needs more
  sources per question — the live path or an LLM key — not a looser rule.

Scores are only comparable within a task-set version; the v1.0 baseline (5
database questions) is in git history.

## The eval flywheel

One benchmark run proves nothing about tomorrow's build, so the head-to-head is
a standing job rather than a one-off:

```bash
uv run python -m app.eval.flywheel --gate --report ../docs/eval-trends.md
```

Every run scores each available engine — moo always, Exa and Tavily when
`EXA_API_KEY` / `TAVILY_API_KEY` are set — on the golden task set, appends a
timestamped record to `api/data/eval_history.jsonl` (machine-local, gitignored),
and rewrites the committed report at [`eval-trends.md`](eval-trends.md). The
report records the conditions of the run, because the same task set scores very
differently store-only versus live, and a trend line that doesn't say which one
it is means nothing.

`--gate` exits non-zero when moo drops below 90% of its own previous score on
coverage or source recall. It compares only against runs of the **same task-set
version**, so growing the golden set can never fire a false regression. CI runs
the gate logic as unit tests on every change and the real head-to-head on a
weekly schedule ([`eval.yml`](../.github/workflows/eval.yml)).

### The USP acceptance gate

A separate harness tests moo's one falsifiable claim: given a query whose
popular answer is out of date, moo returns the current one **and** marks the old
one superseded, where a general web search repeats the stale answer with no
warning.

```bash
uv run python -m app.eval.stale_traps --gate --transcript ../docs/stale-answer-demo.md
```

`stale_trap_tasks.json` holds 18 verified traps — deprecated APIs, changed
defaults, removed flags — each with the stale answer, the current one, and where
the change is documented. An engine passes a trap by surfacing the current answer
and flagging the old one, either structurally (a disputed point, a superseded
claim, a version-outdated source — only moo emits these) or textually (naming the
old thing next to a staleness cue, which any engine can do and which counts
honestly when a competitor does it).

The gate has **three** outcomes, not two. Every trap asks what the live web says
today, so a run with no search provider fetches nothing, scores 0%, and proves
nothing — that returns `not_evaluated` rather than `fail`. A launch gate that
cannot tell "we broke it" from "we never ran it" is not a gate. `--strict` turns
a non-evaluated run into a failure, which is what CI uses, since CI only runs this
when a provider is configured.

### Growing the golden set

`app/eval/deep_research_tasks.json` is meant to grow: add a task when a real
query exposes a gap — a domain moo answers badly, or a question where sources
disagree and moo missed it. The rules live in the file's `growth_process` block;
the short version is: stable kebab-case ids that are never reused, 3–5 lowercase
rubric terms a correct answer contains and a wrong one doesn't, primary-source
hosts in `key_source_hints`, and disputes only where sources genuinely conflict
(an empty `disputed_hints` scores null, not zero). Tasks are authored from the
question, never from moo's output — a task added because moo already passes it
measures nothing. Adding tasks bumps the minor version and starts a new baseline.

## Reproducible demo

`uv run python -m app.eval.demo` drives the full flow end to end (deep_research →
get_claim → fetch_source → list_contradictions). A recorded transcript is in
[`agent-demo.md`](agent-demo.md).

## Note on quality

The LLM stages (plan, claims, evidence linking, synthesis) fall back to
deterministic heuristics when no provider is configured — the pipeline runs end
to end keyless, but claim/answer *quality* is much better with a model. It's
bring-your-own-key: Gemini, OpenAI, Anthropic, or any OpenAI-compatible / local
server (Ollama, vLLM, LM Studio) via `MOO_LLM_PROVIDER` / `MOO_LLM_API_KEY` /
`MOO_LLM_MODEL` / `MOO_LLM_BASE_URL` in `api/.env` (see `api/.env.example`).
