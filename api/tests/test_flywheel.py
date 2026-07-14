"""Eval flywheel: adapters, scoring, history, regression gate (SUP-141)."""

import pytest

from app.eval import competitors, flywheel

# -- adapters (stubbed clients, no network) --------------------------------------

class StubResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class StubClient:
    def __init__(self, payload, status=200):
        self._resp = StubResponse(payload, status)

    def post(self, url, **kw):
        return self._resp


EXA_PAYLOAD = {"results": [
    {"title": "WAL docs", "url": "https://sqlite.org/wal.html", "text": "wal mode " * 100},
]}
TAVILY_PAYLOAD = {"answer": "Use WAL.", "results": [
    {"title": "WAL", "url": "https://sqlite.org/wal.html", "content": "wal journal"},
]}


def test_exa_adapter_normalizes(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "k")
    run = competitors.exa_engine(client=StubClient(EXA_PAYLOAD))
    out = run("sqlite wal")
    assert out["results"][0]["url"] == "https://sqlite.org/wal.html"
    assert len(out["results"][0]["snippet"]) <= 500


def test_tavily_adapter_normalizes(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    run = competitors.tavily_engine(client=StubClient(TAVILY_PAYLOAD))
    out = run("sqlite wal")
    assert out["answer"] == "Use WAL."
    assert out["results"][0]["title"] == "WAL"


def test_adapter_failure_returns_none(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "k")
    run = competitors.exa_engine(client=StubClient({}, status=500))
    assert run("q") is None


def test_engines_keyless_is_moo_only(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert set(competitors.engines(conn=None)) == {"moo"}


# -- scoring -----------------------------------------------------------------------

TASK = {
    "question": "sqlite wal mode",
    "rubric_points": ["wal", "checkpoint", "concurrency"],
    "key_source_hints": ["sqlite.org"],
}


def test_score_engine_output():
    out = {
        "results": [{"title": "WAL", "url": "https://sqlite.org/wal.html",
                     "snippet": "wal checkpoint behavior"}],
        "answer": None,
    }
    s = flywheel.score_engine_output(out, TASK)
    assert s["coverage"] == pytest.approx(2 / 3, rel=1e-2)  # wal + checkpoint, not concurrency
    assert s["source_recall"] == 1.0


def test_run_flywheel_with_fake_engines():
    good = lambda q: {"results": [{"title": "t", "url": "https://sqlite.org/x",
                                   "snippet": "wal checkpoint concurrency"}], "answer": None}
    flaky = lambda q: None
    record = flywheel.run_flywheel(
        conn=None, tasks=[TASK], engine_map={"good": good, "flaky": flaky}
    )
    assert record["engines"]["good"]["coverage"] == 1.0
    assert record["engines"]["good"]["source_recall"] == 1.0
    assert record["engines"]["flaky"]["errors"] == 1
    assert record["at"] and record["task_set_version"]


# -- history + gate ------------------------------------------------------------------

def _record(coverage, recall):
    return {"at": "t", "task_set_version": "1.0",
            "engines": {"moo": {"coverage": coverage, "source_recall": recall,
                                "latency_ms": 1, "tasks": 5, "errors": 0}}}


def test_history_roundtrip(tmp_path):
    path = tmp_path / "hist.jsonl"
    flywheel.append_history(_record(0.5, 0.5), path)
    flywheel.append_history(_record(0.6, 0.6), path)
    hist = flywheel.load_history(path)
    assert [h["engines"]["moo"]["coverage"] for h in hist] == [0.5, 0.6]


def test_history_tolerates_corrupt_lines(tmp_path):
    path = tmp_path / "hist.jsonl"
    flywheel.append_history(_record(0.5, 0.5), path)
    path.write_text(path.read_text(encoding="utf-8") + "{corrupt\n", encoding="utf-8")
    assert len(flywheel.load_history(path)) == 1


def test_gate_passes_first_run():
    assert flywheel.check_regression(_record(0.4, 0.4), history=[]) == []


def test_gate_passes_on_improvement():
    assert flywheel.check_regression(_record(0.6, 0.6), [_record(0.5, 0.5)]) == []


def test_gate_fails_on_regression():
    failures = flywheel.check_regression(_record(0.3, 0.5), [_record(0.5, 0.5)])
    assert len(failures) == 1 and "coverage regressed" in failures[0]


def test_gate_tolerates_small_noise():
    # 0.48 vs 0.5 is within the 10% band — noise, not regression
    assert flywheel.check_regression(_record(0.48, 0.5), [_record(0.5, 0.5)]) == []
