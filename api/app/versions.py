"""Version awareness (SUP-136).

Software answers are version-specific and go stale — "works in Postgres 16,
deprecated since 14, removed in 15". General search engines are version-blind;
moo extracts version signals at serving time and uses them to (a) scope/boost
retrieval when the query names a version and (b) annotate every source with the
version context its text carries, so agents can judge applicability.

Runtime-only by design: content arrives via live fetch, so mentions are
extracted from the retrieved candidates per query — no schema, no staleness.
(SUP-137's temporal evidence graph adds persistence + supersession edges.)

Relations recognized on mentions:

- ``since``       introduced/available/added in X
- ``deprecated``  deprecated in/as of X
- ``removed``     removed/dropped in X
- ``mentions``    bare product-version reference ("PostgreSQL 16 supports ...")
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Products whose versions are worth recognizing. Alias -> canonical name.
# Deliberately generous: unknown products still match the generic vN.N shape
# when preceded by a capitalized word, but these get clean canonical names.
PRODUCT_ALIASES: dict[str, str] = {
    "postgresql": "postgresql", "postgres": "postgresql", "pg": "postgresql",
    "mysql": "mysql", "mariadb": "mariadb", "sqlite": "sqlite",
    "redis": "redis", "mongodb": "mongodb", "python": "python",
    "node": "node", "nodejs": "node", "node.js": "node",
    "django": "django", "fastapi": "fastapi", "flask": "flask",
    "react": "react", "vue": "vue", "angular": "angular",
    "kubernetes": "kubernetes", "k8s": "kubernetes", "docker": "docker",
    "terraform": "terraform", "go": "go", "golang": "go", "rust": "rust",
    "java": "java", "typescript": "typescript", "php": "php", "ruby": "ruby",
    "rails": "rails", "spring": "spring", "linux": "linux", "git": "git",
    "npm": "npm", "pip": "pip", "uv": "uv",
}

_PRODUCT_RE = "|".join(sorted(map(re.escape, PRODUCT_ALIASES), key=len, reverse=True))
_VER = r"v?(\d+(?:\.\d+){0,3})(?:\+|\b)"

# "<product> <version>" and "<product> v1.2" mentions
_MENTION_RE = re.compile(rf"\b({_PRODUCT_RE})\s+{_VER}", re.I)
# relation cues looking *forward* to a mention: "deprecated in Python 3.9"
_RELATION_CUES = [
    ("removed", re.compile(rf"\b(?:removed|dropped|deleted)\s+(?:in|as\s+of|since|from)\s+({_PRODUCT_RE})?\s*{_VER}", re.I)),
    ("deprecated", re.compile(rf"\bdeprecated\s+(?:in|as\s+of|since|from)\s+({_PRODUCT_RE})?\s*{_VER}", re.I)),
    ("since", re.compile(rf"\b(?:introduced|added|available|new|supported)\s+(?:in|as\s+of|since|from)\s+({_PRODUCT_RE})?\s*{_VER}", re.I)),
    ("since", re.compile(rf"\bsince\s+({_PRODUCT_RE})\s+{_VER}", re.I)),
]


@dataclass
class Mention:
    product: str | None
    version: str
    relation: str  # since | deprecated | removed | mentions


def _canon(product: str | None) -> str | None:
    return PRODUCT_ALIASES.get(product.lower()) if product else None


def parse_version(v: str) -> tuple[int, ...]:
    """'16.2' -> (16, 2); tolerant of a leading v."""
    return tuple(int(p) for p in v.lstrip("vV").split("."))


def extract_mentions(text: str, *, limit: int = 12) -> list[Mention]:
    """All product-version mentions in ``text`` with their strongest relation.
    Relation-cued matches (deprecated/removed/since) win over bare mentions of
    the same (product, version)."""
    found: dict[tuple[str | None, str], Mention] = {}
    for relation, pat in _RELATION_CUES:
        for m in pat.finditer(text):
            product, version = _canon(m.group(1)), m.group(2)
            key = (product, version)
            if key not in found:  # cue lists are ordered strongest-first
                found[key] = Mention(product, version, relation)
    for m in _MENTION_RE.finditer(text):
        product, version = _canon(m.group(1)), m.group(2)
        key = (product, version)
        if key not in found:
            found[key] = Mention(product, version, "mentions")
    return list(found.values())[:limit]


def query_constraint(query: str) -> Mention | None:
    """The version constraint a query carries, if any: "json in redis 7",
    "postgres 16 parallel vacuum" -> Mention(product, version, "mentions")."""
    m = _MENTION_RE.search(query)
    if not m:
        return None
    return Mention(_canon(m.group(1)), m.group(2), "mentions")


def matches(mention: Mention, constraint: Mention) -> bool:
    """Does a source mention line up with the query's constraint? Same product
    (or unattributed mention) and same major version — "16.2" satisfies "16"."""
    if mention.product and constraint.product and mention.product != constraint.product:
        return False
    try:
        mv, cv = parse_version(mention.version), parse_version(constraint.version)
    except ValueError:
        return False
    return mv[: len(cv)] == cv or cv[: len(mv)] == mv


MATCH_BOOST = 1.25       # source speaks about the requested version
OUTDATED_PENALTY = 0.75  # source only speaks about older major versions


def apply_constraint(hits: list, constraint: Mention) -> dict[int, dict]:
    """Boost hits matching the query's version constraint, demote hits whose
    text only covers older majors, and re-sort. Returns per-chunk annotations
    ``{chunk_id: {mentions: [...], match, outdated}}`` for the response."""
    annotations: dict[int, dict] = {}
    for h in hits:
        mentions = extract_mentions(h.text)
        info = annotate(constraint, mentions)
        if info["match"]:
            h.score *= MATCH_BOOST
        elif info["outdated"]:
            h.score *= OUTDATED_PENALTY
        annotations[h.chunk_id] = {
            "mentions": [m.__dict__ for m in mentions],
            "match": info["match"],
            "outdated": info["outdated"],
        }
    hits.sort(key=lambda h: -h.score)
    return annotations


def annotate(constraint: Mention, mentions: list[Mention]) -> dict:
    """Version verdict for one source given the query constraint:
    ``{match: bool, outdated: bool}`` — ``outdated`` when the text only speaks
    about strictly older major versions of the same product."""
    same_product = [
        m for m in mentions
        if m.product == constraint.product or m.product is None
    ]
    if not same_product:
        return {"match": False, "outdated": False}
    match = any(matches(m, constraint) for m in same_product)
    try:
        cmajor = parse_version(constraint.version)[0]
        outdated = not match and all(
            parse_version(m.version)[0] < cmajor for m in same_product
        )
    except ValueError:
        outdated = False
    return {"match": match, "outdated": outdated}
