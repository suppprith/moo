"""Caller-defined output schemas for deep_research.

Tavily ``/research``, Exa deep modes, and Firecrawl extract all let the caller
say what JSON shape they want back. moo matches that — and keeps its edge: the
report's findings/claims stay the internal representation, and every populated
field is **traced back to the claims that back it** (``grounding`` maps each
field path to ``clm_`` handles, which drill down to sources via ``get_claim``).

The groundedness guard survives schema-in: a field whose content cannot be
traced to a finding is set to ``null`` and listed in ``ungrounded_fields`` —
never fabricated. Keyless operation degrades to a heuristic fill (fields are
populated with the best-matching finding texts verbatim, so they are grounded
by construction), same as every other LLM stage in moo.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from .. import llm

MAX_PROPERTIES = 40
MAX_DEPTH = 4
ALLOWED_TYPES = {"object", "array", "string", "number", "integer", "boolean"}

GROUND_COVERAGE = 0.6
CITE_COVERAGE = 0.2

_CITATION = re.compile(r"\[S\d+\]")
_WORD = re.compile(r"[a-z0-9]+")

_PROMPT = """You reshape a research report into the caller's JSON schema.

Fill the schema's fields using ONLY the findings below (verbatim or tight
paraphrase). Never add facts that are not in a finding. If no finding covers a
field, set it to null (or [] for arrays). Keep any [S#] citation markers that
appear in text you reuse.

Question: {question}

Executive answer: {answer}

Findings:
{findings}
"""


def validate_schema(schema: dict) -> None:
    """Sanity-check a caller-supplied JSON schema; raises ``ValueError`` with a
    caller-friendly message (the API maps it to 422)."""
    if not isinstance(schema, dict):
        raise ValueError("output_schema must be a JSON-schema object")
    if schema.get("type", "object") != "object" or not isinstance(schema.get("properties"), dict):
        raise ValueError("output_schema must have type 'object' with a 'properties' map")
    if not schema["properties"]:
        raise ValueError("output_schema.properties must not be empty")

    count = 0

    def walk(node: dict, depth: int) -> None:
        nonlocal count
        if depth > MAX_DEPTH:
            raise ValueError(f"output_schema exceeds max nesting depth {MAX_DEPTH}")
        t = node.get("type", "string")
        if t not in ALLOWED_TYPES:
            raise ValueError(f"unsupported schema type {t!r}; allowed: {sorted(ALLOWED_TYPES)}")
        if t == "object":
            for child in (node.get("properties") or {}).values():
                if not isinstance(child, dict):
                    raise ValueError("every schema property must be an object")
                walk(child, depth + 1)
        elif t == "array":
            items = node.get("items") or {"type": "string"}
            if not isinstance(items, dict):
                raise ValueError("array 'items' must be a schema object")
            walk(items, depth + 1)
        else:
            count += 1
            if count > MAX_PROPERTIES:
                raise ValueError(f"output_schema has more than {MAX_PROPERTIES} fields")

    walk(schema, 0)


def _words(text: str) -> set[str]:
    return set(_WORD.findall(_CITATION.sub(" ", str(text)).lower()))


def _ground_value(value, findings: list[dict]) -> tuple[bool, list[str]]:
    """Is ``value``'s content covered by the findings, and by which claims?
    Returns ``(grounded, [clm_ handles])``. Non-text scalars ground when they
    appear verbatim in some finding."""
    if value is None:
        return True, []
    if isinstance(value, bool) or isinstance(value, (int, float)):
        token = str(value).lower().rstrip("0").rstrip(".") if isinstance(value, float) else str(value).lower()
        backing = [f["claim"] for f in findings if token in f["text"].lower()]
        return bool(backing), backing[:3]
    words = _words(value)
    if not words:
        return True, []
    covered: set[str] = set()
    backing: list[tuple[float, str]] = []
    for f in findings:
        fwords = _words(f["text"])
        overlap = words & fwords
        if overlap:
            share = len(overlap) / len(words)
            if share >= CITE_COVERAGE:
                backing.append((share, f["claim"]))
            covered |= overlap
    grounded = len(covered) / len(words) >= GROUND_COVERAGE
    backing.sort(reverse=True)
    return grounded, [h for _, h in backing[:5]]


def _ground_output(output, schema: dict, findings: list[dict]):
    """Walk the filled output against its schema; null out ungrounded leaves.
    Returns ``(output, grounding, ungrounded_paths)``."""
    grounding: dict[str, list[str]] = {}
    ungrounded: list[str] = []

    def walk(value, node: dict, path: str):
        t = node.get("type", "string")
        if t == "object" and isinstance(value, dict):
            props = node.get("properties") or {}
            return {k: walk(v, props.get(k, {}), f"{path}.{k}") if k in props else v
                    for k, v in value.items()}
        if t == "array" and isinstance(value, list):
            items = node.get("items") or {"type": "string"}
            kept = []
            for i, v in enumerate(value):
                kept.append(walk(v, items, f"{path}[{i}]"))
            return kept
        grounded, backing = _ground_value(value, findings)
        if not grounded:
            ungrounded.append(path)
            return None
        if backing:
            grounding[path] = backing
        return value

    out = walk(output, schema, "$")
    return out, grounding, ungrounded


def _llm_fill(conn: sqlite3.Connection, report: dict, schema: dict):
    findings_txt = "\n".join(
        f"- ({f['claim']}, confidence {f.get('confidence')}) {f['text']}"
        for f in report.get("findings", [])
    )
    key = json.dumps(schema, sort_keys=True) + "|" + "|".join(
        f["claim"] for f in report.get("findings", [])
    )
    return llm.cached_json(
        conn, "structured",
        hashlib.sha256(key.encode()).hexdigest() + report.get("question", ""),
        _PROMPT.format(
            question=report.get("question", ""),
            answer=report.get("executive_answer") or "(none)",
            findings=findings_txt or "(none)",
        ),
        schema=schema,
        max_tokens=2000,
    )


def _score_match(name: str, node: dict, finding: dict) -> float:
    hint = _words(name.replace("_", " ") + " " + (node.get("description") or ""))
    if not hint:
        return 0.0
    return len(hint & _words(finding["text"])) / len(hint)


def _heuristic_fill(report: dict, schema: dict):
    """Keyless fill: populate string fields with the best-matching finding text
    verbatim (grounded by construction); summary-ish fields get the executive
    answer; everything untextual stays null."""
    findings = report.get("findings", [])

    def fill(node: dict, name: str):
        t = node.get("type", "string")
        if t == "object":
            return {k: fill(child, k) for k, child in (node.get("properties") or {}).items()}
        if t == "array":
            items = node.get("items") or {"type": "string"}
            if items.get("type", "string") == "string":
                ranked = sorted(findings, key=lambda f: _score_match(name, node, f), reverse=True)
                return [f["text"] for f in ranked[:3]]
            return []
        if t == "string":
            if re.search(r"summary|answer|overview|conclusion", name, re.I):
                return report.get("executive_answer") or None
            best = max(findings, key=lambda f: _score_match(name, node, f), default=None)
            if best is not None and _score_match(name, node, best) > 0:
                return best["text"]
            return None
        return None

    return {k: fill(child, k) for k, child in schema["properties"].items()}


def structure_report(
    conn: sqlite3.Connection, report: dict, output_schema: dict, *, use_llm: bool = True
) -> dict:
    """Map an assembled report into the caller's schema, with per-field claim
    grounding. Returns ``{output, grounding, ungrounded_fields, generator}``;
    attach it to the report as ``report["structured"]``."""
    validate_schema(output_schema)
    findings = report.get("findings", [])
    filled = None
    generator = "heuristic"
    if use_llm and findings:
        filled = _llm_fill(conn, report, output_schema)
        if isinstance(filled, dict):
            generator = "model"
        else:
            filled = None
    if filled is None:
        filled = _heuristic_fill(report, output_schema)

    output, grounding, ungrounded = _ground_output(filled, output_schema, findings)
    result: dict = {
        "output": output,
        "grounding": grounding,
        "ungrounded_fields": ungrounded,
        "generator": generator,
    }
    if ungrounded:
        result["note"] = (
            "fields set to null could not be traced to any finding and were "
            "not fabricated"
        )
    return result
