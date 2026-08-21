"""Polarity opposition: does this text assert the opposite of that one?

Contradiction is moo's differentiator, and without an LLM key it was detected
by looking for a contrast word — "but", "however", "not", "unlike" — anywhere
in a topically related chunk. Ordinary technical prose is full of those, so the
rule fired on almost anything and the resulting edges were noise: 305
"contradicts" edges over a 79-document store, none of which pointed at a real
disagreement (``contradiction_recall`` stayed at 0.0).

Real opposition needs two things at once, and the cue-word rule tested neither:

1. the two texts are **about the same thing** — that is what embedding
   similarity already measures, and callers pass it in as ``sim``;
2. they **say opposite things about it** — which is what this module decides:

   - **negation mismatch** — the same assertion, one side negated
     ("SQLite handles concurrent writes" / "SQLite does not handle concurrent
     writes"), requiring real lexical overlap so it is the same assertion and
     not two unrelated sentences that happen to share a topic;
   - **antonym conflict** — one side says faster/safe/recommended where the
     other says slower/unsafe/deprecated, about a shared subject.

Everything here is a pure function over text, so the rules are unit-testable
without a database, an index, or a model.
"""

from __future__ import annotations

import re

# Same subject: below this embedding similarity the texts are not talking about
# the same thing and any polarity difference is a coincidence.
SUBJECT_SIM = 0.45

# A negation mismatch is only the *same assertion* negated when the two texts
# genuinely restate each other; antonym pairs carry their own opposition, so
# they need less overlap to be believable.
NEGATION_OVERLAP = 0.34
ANTONYM_OVERLAP = 0.12

_NEGATION_TERMS = (
    "not", "no", "never", "none", "cannot", "can't", "cant", "doesn't", "does not",
    "don't", "isn't", "is not", "aren't", "are not", "won't", "will not", "shouldn't",
    "should not", "wouldn't", "couldn't", "without", "nor", "unable", "fails to",
    "failed to", "lacks", "lack of",
)
_NEGATION = re.compile(r"\b(" + "|".join(re.escape(t) for t in _NEGATION_TERMS) + r")\b", re.I)

# Each pair is two sides of one axis. A text that mentions *both* sides is
# comparing them, not asserting one, so it never counts as opposition.
#
# Terms have to be *load-bearing*: a word common enough to appear in any two
# paragraphs about the same topic ("can", "must", "supports") turns every pair
# of related texts into a contradiction, which is the exact failure this module
# was written to end. When in doubt, leave the term out — a missed dispute is
# recoverable, a store full of fake ones is not.
ANTONYMS: tuple[tuple[frozenset[str], frozenset[str]], ...] = tuple(
    (frozenset(a), frozenset(b))
    for a, b in (
        ({"faster", "quicker", "speedup", "speeds up"}, {"slower", "slowdown", "slows down"}),
        ({"better", "superior"}, {"worse", "inferior"}),
        ({"thread-safe", "threadsafe"}, {"unsafe", "race condition", "data race"}),
        (
            {"recommended", "preferred", "idiomatic", "best practice"},
            {"discouraged", "deprecated", "anti-pattern", "antipattern", "obsolete"},
        ),
        (
            {"still supported", "still works", "still available"},
            {"unsupported", "was removed", "has been removed", "no longer works",
             "no longer supported"},
        ),
        ({"required", "mandatory"}, {"optional", "unnecessary", "not needed"}),
        ({"enabled by default", "on by default"}, {"disabled by default", "off by default"}),
        ({"production-ready", "production ready"}, {"experimental", "unstable", "not production"}),
        ({"increases", "higher"}, {"decreases", "lower"}),
        ({"always"}, {"never"}),
        ({"durable", "persisted"}, {"lossy", "ephemeral", "volatile"}),
    )
)

_STOPWORDS = frozenset(
    """a an and are as at be been being but by for from has have how if in into is it its of on
    or over so than that the their then there these they this to under until was were what when
    where which while who why will with you your""".split()
)

_WORD = re.compile(r"[a-z0-9][a-z0-9_+.#-]*")

# Polarity vocabulary never counts as subject matter: otherwise two texts look
# like they share a subject because they both happen to say "not".
_POLARITY_WORDS = frozenset(
    word
    for term in [t for group in ANTONYMS for side in group for t in side] + list(_NEGATION_TERMS)
    for word in _WORD.findall(term.lower())
)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def content_terms(text: str) -> frozenset[str]:
    """Subject words: what the text is about, minus the polarity vocabulary."""
    return frozenset(
        w
        for w in _WORD.findall(text.lower())
        if len(w) >= 3 and w not in _STOPWORDS and w not in _POLARITY_WORDS
    )


def overlap(a: str, b: str) -> float:
    """Jaccard overlap of subject words. 0 when either side has no subject."""
    ta, tb = content_terms(a), content_terms(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_negated(text: str) -> bool:
    return bool(_NEGATION.search(text))


def antonym_conflict(a: str, b: str) -> tuple[str, str] | None:
    """The first axis on which ``a`` and ``b`` take opposite sides, if any.

    A text that mentions both sides of an axis is weighing them up, so it is
    skipped: "X is faster for reads but slower for writes" contradicts nobody."""
    na, nb = _norm(a), _norm(b)

    def has(text: str, side: frozenset[str]) -> bool:
        return any(re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", text) for term in side)

    for left, right in ANTONYMS:
        a_left, a_right = has(na, left), has(na, right)
        b_left, b_right = has(nb, left), has(nb, right)
        if a_left and a_right or b_left and b_right:
            continue  # one side is comparing, not asserting
        if (a_left and b_right) or (a_right and b_left):
            side_a = sorted(left if a_left else right)[0]
            side_b = sorted(right if a_left else left)[0]
            return side_a, side_b
    return None


def opposes(a: str, b: str, *, sim: float) -> tuple[str, float] | None:
    """Whether ``b`` asserts the opposite of ``a``, given their similarity.

    Returns ``(kind, strength)`` — ``kind`` is ``"antonym"`` or ``"negation"``
    — or ``None``. ``sim`` is the caller's embedding similarity and gates the
    "same subject" half: without it, opposite polarity about two unrelated
    topics would read as a contradiction."""
    if sim < SUBJECT_SIM or not a.strip() or not b.strip():
        return None

    shared = overlap(a, b)
    if antonym_conflict(a, b) and shared >= ANTONYM_OVERLAP:
        return "antonym", round(min(0.95, 0.40 + 0.50 * sim), 3)
    if is_negated(a) != is_negated(b) and shared >= NEGATION_OVERLAP:
        return "negation", round(min(0.90, 0.30 + 0.45 * sim), 3)
    return None
