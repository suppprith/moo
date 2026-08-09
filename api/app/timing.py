"""Per-stage latency and LLM-cost accounting for the pipelines.

Every heavy stage of `search()` runs inside `Timings.stage(...)`, which records
both the wall-clock time and the number of LLM calls the stage attempted. That
lands on the response as `meta.timings_ms` + `meta.cost`, gets logged per
request, and is what the latency eval and the ops metrics both read.

Budgets are warm-path targets (models loaded, cache primed): the retrieval-only
path is the one an agent hits on every call, so it carries the tightest one. A
stage set that blows its budget is logged at WARNING with the breakdown, so a
regression shows up in the log before anyone runs the eval.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

log = logging.getLogger("moo.timing")

BUDGET_MS = {"raw": 1000.0, "claims": 5000.0, "full": 8000.0}


def budget_for(mode: str) -> float | None:
    return BUDGET_MS.get(mode)


class Timings:
    """Stage stopwatch + LLM-call attribution.

    `counter` returns a monotonically increasing count of attempted LLM calls
    (`llm.attempts()`); the delta across a stage is that stage's model cost.
    Passing None disables cost attribution.
    """

    def __init__(self, counter=None):
        self._counter = counter
        self._ms: dict[str, float] = {}
        self._llm: dict[str, int] = {}
        self._started = time.perf_counter()

    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter()
        c0 = self._counter() if self._counter else 0
        try:
            yield
        finally:
            self.add(name, (time.perf_counter() - t0) * 1000)
            if self._counter:
                spent = self._counter() - c0
                if spent:
                    self._llm[name] = self._llm.get(name, 0) + spent

    def add(self, name: str, ms: float) -> None:
        self._ms[name] = self._ms.get(name, 0.0) + ms

    @property
    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._started) * 1000, 1)

    def as_dict(self) -> dict[str, float]:
        return {name: round(ms, 1) for name, ms in self._ms.items()}

    def llm_by_stage(self) -> dict[str, int]:
        return dict(self._llm)

    def slowest(self) -> tuple[str, float] | None:
        if not self._ms:
            return None
        name = max(self._ms, key=lambda k: self._ms[k])
        return name, round(self._ms[name], 1)

    def report(self, label: str, budget_ms: float | None = None) -> float:
        """Log the breakdown and return the total. Over budget logs a warning."""
        total = self.elapsed_ms
        breakdown = " ".join(f"{n}={ms:.0f}ms" for n, ms in self.as_dict().items())
        if budget_ms is not None and total > budget_ms:
            slowest = self.slowest()
            log.warning(
                "%s %.0fms over budget %.0fms (slowest %s) %s",
                label, total, budget_ms, slowest[0] if slowest else "?", breakdown,
            )
        else:
            log.info("%s %.0fms %s", label, total, breakdown)
        return total
