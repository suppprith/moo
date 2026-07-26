"""Temporal claim validity + supersession.

Extends the evidence graph in the dimension general search engines can't
follow: *when* a claim is true. Two passes over freshly scored claims:

1. **annotate_versions** — extract version validity from each claim's text
   (via ``app.versions``): a ``since`` mention sets ``valid_from``, a
   ``deprecated``/``removed`` mention sets ``valid_until``, a bare product
   mention sets the product context.

2. **link_supersessions** — two claims about the *same subject* (embedding
   cosine >= SIMILARITY) for the *same product* at *different versions* are a
   version chain, not a disagreement: the newer claim ``supersedes`` the older
   (``claim_link`` edge), and if the older one was marked disputed, that flag
   is cleared — it isn't controversial, it's historical. Live contradictions
   (no version separation) keep their disputed flag untouched.

Called from the evidence path after confidence scoring (search claims mode +
research loop). Cheap: claims per query are few and their embeddings are one
batched encode.
"""

from __future__ import annotations

import logging
import sqlite3

from .. import versions

log = logging.getLogger("moo.evidence.temporal")

SIMILARITY = 0.80


def annotate_versions(conn: sqlite3.Connection, claims: list[dict]) -> int:
    """Set valid_product / valid_from / valid_until on claims whose text
    carries version signals. Returns claims annotated."""
    annotated = 0
    for c in claims:
        mentions = versions.extract_mentions(c["text"])
        if not mentions:
            continue
        product = next((m.product for m in mentions if m.product), None)
        valid_from = valid_until = None
        for m in mentions:
            if m.relation == "since" and valid_from is None:
                valid_from = m.version
            elif m.relation in ("deprecated", "removed") and valid_until is None:
                valid_until = m.version
        if valid_from is None and valid_until is None:
            valid_from = mentions[0].version
        conn.execute(
            "UPDATE claim SET valid_product = ?, valid_from = ?, valid_until = ? WHERE id = ?",
            (product, valid_from, valid_until, c["id"]),
        )
        annotated += 1
    conn.commit()
    return annotated


def _claim_version(row: sqlite3.Row) -> tuple[int, ...] | None:
    """The version a claim speaks *as of* — its valid_from, else valid_until."""
    for field in ("valid_from", "valid_until"):
        if row[field]:
            try:
                return versions.parse_version(row[field])
            except ValueError:
                continue
    return None


def link_supersessions(conn: sqlite3.Connection, claim_ids: list[int]) -> list[dict]:
    """Find same-subject, same-product claim pairs at different versions among
    ``claim_ids`` (plus their existing same-product neighbors) and record the
    newer one as superseding the older. Clears ``disputed`` on superseded
    claims. Returns the links created."""
    if not claim_ids:
        return []
    qmarks = ",".join("?" * len(claim_ids))
    rows = conn.execute(
        f"""SELECT id, text, disputed, valid_product, valid_from, valid_until
            FROM claim
            WHERE valid_product IS NOT NULL
              AND (id IN ({qmarks})
                   OR valid_product IN (SELECT DISTINCT valid_product FROM claim
                                        WHERE id IN ({qmarks}) AND valid_product IS NOT NULL))""",
        claim_ids + claim_ids,
    ).fetchall()
    candidates = [r for r in rows if _claim_version(r) is not None]
    if len(candidates) < 2:
        return []

    from ..embed import embed_texts

    vecs = embed_texts([r["text"] for r in candidates])
    links: list[dict] = []
    for i, a in enumerate(candidates):
        for j in range(i + 1, len(candidates)):
            b = candidates[j]
            if a["valid_product"] != b["valid_product"]:
                continue
            va, vb = _claim_version(a), _claim_version(b)
            if va[: len(vb)] == vb or vb[: len(va)] == va:
                continue
            if float(vecs[i] @ vecs[j]) < SIMILARITY:
                continue
            newer, older = (a, b) if va > vb else (b, a)
            conn.execute(
                "INSERT OR IGNORE INTO claim_link (claim_id, target_claim_id, relation, rationale) "
                "VALUES (?, ?, 'supersedes', ?)",
                (newer["id"], older["id"],
                 f"{newer['valid_product']} {'.'.join(map(str, max(va, vb)))} supersedes "
                 f"{'.'.join(map(str, min(va, vb)))}"),
            )
            if older["disputed"]:
                conn.execute("UPDATE claim SET disputed = 0 WHERE id = ?", (older["id"],))
                log.info("claim %d un-disputed: superseded by %d", older["id"], newer["id"])
            links.append({"claim_id": newer["id"], "target_claim_id": older["id"]})
    conn.commit()
    return links


def process(conn: sqlite3.Connection, claims: list[dict]) -> dict:
    """annotate then link, for one batch of freshly scored claims."""
    annotated = annotate_versions(conn, claims)
    links = link_supersessions(conn, [c["id"] for c in claims]) if annotated else []
    return {"annotated": annotated, "superseded": len(links)}


def superseded_by(conn: sqlite3.Connection, claim_ids: list[int]) -> dict[int, int]:
    """{old_claim_id: superseding_claim_id} for the given claims."""
    if not claim_ids:
        return {}
    qmarks = ",".join("?" * len(claim_ids))
    return {
        r["target_claim_id"]: r["claim_id"]
        for r in conn.execute(
            f"SELECT claim_id, target_claim_id FROM claim_link "
            f"WHERE relation = 'supersedes' AND target_claim_id IN ({qmarks})",
            claim_ids,
        )
    }
