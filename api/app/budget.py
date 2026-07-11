"""Token-budget-aware result shaping for agent tools (SUP-117).

Agents have finite context, so a tool result must fit a caller-supplied budget
and offer drill-down handles instead of dumping the corpus. Token counts are a
cheap char-based estimate (~4 chars/token; no tokenizer dependency) — good
enough to keep payloads bounded.

Truncation is always **explicit** (a `truncation` marker / `text_truncated`
note), and never silently drops evidence that changes a conclusion: report
shaping keeps `disputed_points` in full so contradictions always surface, and
budgets only the supporting `findings`/`sources`.
"""

from __future__ import annotations

import json

CHARS_PER_TOKEN = 4
DEFAULT_MAX_TOKENS = 4000


def estimate_tokens(obj) -> int:
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str, ensure_ascii=False)
    return max(1, len(text) // CHARS_PER_TOKEN)


def clamp_text(text: str | None, max_tokens: int) -> tuple[str | None, bool, int]:
    """Return (text, truncated, original_char_len), trimming to the char budget."""
    if not text:
        return text, False, 0
    limit = max_tokens * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text, False, len(text)
    return text[:limit].rstrip() + "…", True, len(text)


def pack(items: list, max_tokens: int) -> tuple[list, int]:
    """Greedily keep items (in order) whose cumulative estimate fits `max_tokens`.
    Always keeps at least one item so a single large item still returns something.
    Returns (kept, omitted_count)."""
    kept: list = []
    used = 0
    omitted = 0
    for it in items:
        t = estimate_tokens(it)
        if not kept or used + t <= max_tokens:
            kept.append(it)
            used += t
        else:
            omitted += 1
    return kept, omitted


def shape_search(result: dict, max_tokens: int) -> dict:
    """Cap the `sources` list to the budget; on truncation point at the existing
    pagination cursor so the agent can fetch the next page."""
    kept, omitted = pack(result.get("sources", []), max_tokens)
    result["sources"] = kept
    if omitted:
        cursor = ((result.get("meta") or {}).get("page") or {}).get("next_cursor")
        result["truncation"] = {
            "sources_omitted": omitted,
            "next_cursor": cursor,
            "note": "more results available: page with cursor/offset, or fetch_source on a handle",
        }
    return result


def shape_graph(result: dict, max_tokens: int) -> dict:
    nodes, n_om = pack(result.get("nodes", []), max_tokens // 2)
    edges, e_om = pack(result.get("edges", []), max_tokens // 2)
    result["nodes"], result["edges"] = nodes, edges
    if n_om or e_om:
        result["truncation"] = {
            "nodes_omitted": n_om, "edges_omitted": e_om,
            "note": "expand_graph on a specific node for its neighborhood",
        }
    return result


def shape_report(report: dict, max_tokens: int) -> dict:
    """Keep the executive answer, disputed points, and open questions in full
    (small + conclusion-changing); budget only findings + sources, splitting the
    remaining budget between them. Omitted items remain reachable via the run
    (research_status) and their handles (get_claim / fetch_source)."""
    always_keys = (
        "question", "executive_answer", "disputed_points", "open_questions",
        "generator", "run_id", "status", "partial",
    )
    reserved = estimate_tokens({k: report.get(k) for k in always_keys if k in report})
    remaining = max(max_tokens - reserved, max_tokens // 4)
    per = remaining // 2

    findings, f_omitted = pack(report.get("findings", []), per)
    sources, s_omitted = pack(report.get("sources", []), per)
    report["findings"], report["sources"] = findings, sources
    if f_omitted or s_omitted:
        report["truncation"] = {
            "findings_omitted": f_omitted,
            "sources_omitted": s_omitted,
            "note": "run persisted: research_status(run_id) for the full report; "
                    "get_claim / fetch_source on a handle to drill in",
        }
    return report


def clamp_row_text(row: dict, max_tokens: int) -> dict:
    """Trim a fetched row's `text` field to the budget, marking it explicitly."""
    text, truncated, total = clamp_text(row.get("text"), max_tokens)
    if truncated:
        row["text"] = text
        row["text_truncated"] = {"original_chars": total, "note": "trimmed to token budget"}
    return row
