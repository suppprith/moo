# moo for AI agents

moo is a **CS/coding-specialized search backend for AI agents** — the tool a
coding agent routes its `web_search` to for software-engineering questions
(databases, languages, frameworks, build tooling, errors, systems). It returns
grounded, structured evidence — claims with confidence, supports/contradicts
edges, source trust, and stable handles to drill into — not ten blue links or one
unverifiable paragraph.

## Two ways in

1. **Primitive tools** — the agent drives its own loop: `search`, `fetch_source`,
   `get_claim`, `list_contradictions`, `expand_graph`.
2. **Hosted deep research** — hand off a whole question: `deep_research` runs
   plan → iterative multi-hop retrieval → a cited, confidence-scored report;
   `research_status` polls/resumes it.

Both are exposed over **MCP** (`app/mcp_server.py`) and, for `search`, over a
drop-in **`web_search`** HTTP adapter (`/v1/web_search`). See the
[2-minute quickstart](../README.md#connect-an-agent-mcp-in-2-minutes).

## Tool reference (MCP)

| Tool | Params | Returns / use |
| ---- | ------ | ------------- |
| `search` | `query, mode=raw\|claims\|full, k, fields, max_tokens` | Ranked `sources` (opaque `id` handle, deep-link `url`, `source_type`, trust, score). `raw` = fast, no LLM; `claims` = + claims/evidence/graph; `full` = + cited answer. |
| `fetch_source` | `handle, max_tokens` | Resolve a `chk_`/`doc_`/`clm_` handle to its full row: chunk text + context, whole document, or a claim + evidence. How you go from a citation to the evidence. |
| `get_claim` | `handle` | One claim by `clm_` handle: confidence, disputed flag, full evidence set (contradictions first) with backing source handles. |
| `list_contradictions` | `query, k` | Only the disputed/contradicted claims for a query — where sources disagree. |
| `expand_graph` | `node` | Entity neighborhood (by `ent_` handle or name): typed relations, claim-annotated. |
| `deep_research` | `question, k, max_steps, max_seconds, max_tokens` | `{run_id, status, partial, executive_answer, findings[], disputed_points[], open_questions[], sources[], groundedness}`. Streams MCP progress. |
| `research_status` | `run_id, max_tokens` | Re-fetch a run's status + report (poll a long run or re-read a finished one). |

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

## Groundedness guarantee

Nothing in a report is asserted without a traceable source. Claims are grounded
in their source chunks at extraction; the report carries a `groundedness` summary
and flags any finding that fails citation integrity. Uncited claims never ship.

## Benchmark

`app/eval/deep_research_tasks.json` + `uv run python -m app.eval.benchmark`.
Recorded baseline (heuristic, no LLM key — [`baseline.json`](../api/app/eval/baseline.json)),
single-shot `search` vs. `deep_research`:

| metric | search | deep_research |
| --- | --- | --- |
| coverage | 0.40 | 0.42 |
| citation_accuracy | — | 1.00 |
| source_recall | 0.00 | 0.47 |
| contradiction_recall | 0.00 | 0.00 |
| tokens | 734 | 1676 |

deep_research wins on source recall and grounding; contradiction recall is the
gap to close (needs the LLM evidence linker + a broader corpus).

## Reproducible demo

`uv run python -m app.eval.demo` drives the full flow end to end (deep_research →
get_claim → fetch_source → list_contradictions). A recorded transcript is in
[`agent-demo.md`](agent-demo.md).

## Note on quality

The LLM stages (plan, claims, evidence linking, synthesis) fall back to
deterministic heuristics when no key is set — the pipeline runs end to end
keyless, but claim/answer *quality* is much better with a key. Set
`GEMINI_API_KEY` in `api/.env` to enable the Gemini path.
