# Latency and cost

What a call costs you in time and in model spend, measured rather than claimed.
Every response carries its own numbers, so you never have to take this page's
word for it: `meta.timings_ms` is the per-stage breakdown and `meta.cost` is the
model spend for that one call.

```json
"meta": {
  "elapsed_ms": 141.2,
  "timings_ms": { "understand": 0.4, "expand": 0.3, "retrieve": 139.8 },
  "cost": { "llm_calls": 0, "llm_attempts": 0, "cache_hits": 0,
            "cache_misses": 0, "prompt_chars": 0 }
}
```

`llm_calls` is provider requests actually issued. `llm_attempts` counts the
stages that wanted one, so a keyless instance still reports its model demand and
you can see what turning a key on would cost. When a stage does call a model,
`cost.by_stage` says which one.

## Budgets

| mode | what it does | budget (warm) |
| --- | --- | --- |
| `raw` | retrieval only, zero model calls | 1s |
| `claims` | plus evidence, confidence, contradictions | 5s |
| `full` | plus a cited synthesized answer | 8s |

These are targets, not promises, and they are enforced only in the sense that
missing one logs a warning with the stage breakdown:

```
WARNING moo.timing search mode=raw 15902ms over budget 1000ms (slowest retrieve)
  understand=0ms expand=1ms retrieve=15894ms
```

That line above is a real one, and it is the honest shape of a cold start: the
first search in a process pays about 14 seconds to load the embedding model.
Set `MOO_PRELOAD=1` and the model loads in a background thread at boot instead,
which is what the container image does.

## Measured

14 judged queries, store-only, no search provider and no LLM key, on a laptop
CPU. Reproduce with `uv run python -m app.eval.latency`; the numbers below live
in `api/app/eval/latency_baseline.json`.

| mode | warm p50 | warm p95 | budget | model calls per cold query | input tokens per cold query |
| --- | --- | --- | --- | --- | --- |
| `raw` | 138ms | 188ms | 1s | 0 | 0 |
| `claims` | 2149ms | 2545ms | 5s | 13.7 | 15300 |
| `full` | 2359ms | 2646ms | 8s | 14.7 | 15800 |

Where the time goes, warm: retrieval is a steady 140 to 190ms in every mode, and
everything above `raw` is dominated by the evidence stage at about 2 seconds.
That stage is embedding-bound rather than model-bound, since claim merging and
the grounding check each embed on CPU. Reranking, the graph and synthesis cost
single-digit milliseconds keyless, because without a provider they run their
deterministic fallbacks.

## What a cold query costs

`raw` costs nothing. It never touches a model, and that is the mode an agent
hits on every call.

`claims` and `full` want roughly 14 model calls carrying about 15k input tokens
for a query nobody has asked before. Output is not measured, so price it at the
1024-token cap per call for a ceiling. With your provider's rates:

```
upper bound per cold query = 15000/1e6 * $in_per_mtok + 15000/1e6 * $out_per_mtok
```

The eval will do that arithmetic if you hand it your rates, which change often
enough that this page deliberately does not guess them:

```bash
uv run python -m app.eval.latency --in-price 0.10 --out-price 0.40
```

## The second time is free

Every model stage caches by content hash, so an identical query never pays
twice. Expansion, reranking, claim extraction, evidence linking, synthesis and
research planning all read the cache before the provider.

The cache is only provable with a provider attached, so the eval can fake one:

```bash
uv run python -m app.eval.latency --stub-provider
```

That installs a provider which answers every stage with an empty payload. The
stages fall back to their heuristics exactly as they do keyless, but a result
gets cached, and the second pass over the same queries reports:

```
mode      cold p50  cold p95  warm p50  warm p95   budget   verdict    repeat
raw            146       181       140       181     1000    within      free
claims        1960      2223      2128      2475     5000    within      free
full          2116      2544      2135      2637     8000    within      free
```

`repeat: free` means the second pass issued zero model calls. Rows the stub
wrote are stamped and deleted when the run ends, so a fake answer can never be
served to a real one.

Running that measurement is what turned up a leak worth knowing about: the
reranker used to cache only responses it could parse, so a model that answered
in the wrong shape was re-paid for on every repeat of that query, forever. An
unusable response is now cached as unusable, and reranking falls through to the
fused order without calling anyone.

Two caveats on reading the cold column. Modes share the cache, so running
`claims` before `full` leaves `full` looking cheaper than it is; measure one
mode at a time for a true cold number. And keyless, nothing caches at all,
because a heuristic fallback is not a model result worth storing, which is why
the keyless baseline shows the same demand on every pass while costing nothing.

## Live retrieval

The numbers above are store-only. With a search provider configured, each search
also fetches pages from the live web, which adds a `live` stage bounded by
`MOO_LIVE_MAX_SECONDS` (12s by default) and reported in `meta.live.timings_ms`.
Pages already in the cache and still inside their TTL skip the network
completely, so a warm topic stays fast.
