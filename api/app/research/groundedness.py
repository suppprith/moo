"""Groundedness / faithfulness guard for research reports (SUP-120).

The trust guarantee for agent output: nothing in a report is asserted without a
traceable source. Claims are grounded in their source chunks at *extraction*
time (the GROUND_COSINE guard in `app.evidence.claims`); this pass verifies
**citation integrity** on the assembled report and attaches a groundedness
summary the agent can inspect:

- every finding must cite >=1 real source that actually *backs* the claim (the
  cited document is one the claim was extracted from or linked to) — otherwise
  the finding is flagged ``grounded: false``;
- the executive answer may only cite sources that exist;
- a ``groundedness`` summary reports how many findings are grounded and how many
  have strong (>=2 independent documents) support.

Uncited claims were already excluded by the report assembler; this makes the
guarantee explicit and testable (a well-formed run has zero ungrounded findings).
"""

from __future__ import annotations

import re
import sqlite3

from .. import ids

_CITE = re.compile(r"\[S(\d+)\]")
WELL_SUPPORTED_MIN = 2  # independent backing documents for "strong" support


def _backing_documents(conn: sqlite3.Connection, claim_id: int) -> set[int]:
    """Documents that back a claim: via evidence edges and extraction provenance,
    collapsing near-dup chunks onto their canonical document."""
    rows = conn.execute(
        """
        SELECT COALESCE(canon.document_id, ch.document_id) AS doc_id
        FROM evidence e JOIN chunk ch ON ch.id = e.chunk_id
        LEFT JOIN chunk canon ON canon.id = ch.canonical_chunk_id
        WHERE e.claim_id = ?
        UNION
        SELECT COALESCE(canon.document_id, ch.document_id)
        FROM claim_chunk cc JOIN chunk ch ON ch.id = cc.chunk_id
        LEFT JOIN chunk canon ON canon.id = ch.canonical_chunk_id
        WHERE cc.claim_id = ?
        """,
        (claim_id, claim_id),
    ).fetchall()
    return {r[0] for r in rows if r[0] is not None}


def attach_groundedness(conn: sqlite3.Connection, report: dict) -> dict:
    """Flag each finding grounded/ungrounded, verify the answer's citations, and
    attach a ``groundedness`` summary. Mutates and returns `report`."""
    sources = report.get("sources", [])
    idx_to_doc = {s["index"]: s.get("document_id") for s in sources}
    valid_idx = set(idx_to_doc)
    findings = report.get("findings", [])

    grounded_count = 0
    well_supported = 0
    for f in findings:
        cites = [c for c in f.get("citations", []) if c in valid_idx]
        try:
            claim_id = ids.decode(f["claim"])[1]
            backing = _backing_documents(conn, claim_id)
        except (ValueError, KeyError):
            backing = set()
        cited_docs = {idx_to_doc[c] for c in cites}
        # citation integrity: a cited source must actually back the claim
        f["grounded"] = bool(cites) and bool(cited_docs & backing)
        f["independent_support"] = len(backing)
        if f["grounded"]:
            grounded_count += 1
        if len(backing) >= WELL_SUPPORTED_MIN:
            well_supported += 1

    answer_cites = {int(m) for m in _CITE.findall(report.get("executive_answer") or "")}
    total = len(findings)
    report["groundedness"] = {
        "findings_total": total,
        "findings_grounded": grounded_count,
        "pct_grounded": round(grounded_count / total, 3) if total else 1.0,
        "well_supported": well_supported,
        "answer_citations_valid": answer_cites <= valid_idx,
        "ungrounded": [f["claim"] for f in findings if not f.get("grounded")],
    }
    return report
