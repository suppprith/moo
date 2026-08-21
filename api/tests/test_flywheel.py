"""Eval flywheel: adapters, scoring, history, regression gate."""

import pytest

from app.eval import competitors, flywheel


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
    assert s["coverage"] == pytest.approx(2 / 3, rel=1e-2)
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


def _record(coverage, recall, version="1.0", at="t"):
    return {"at": at, "task_set_version": version, "n_tasks": 5,
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
    assert flywheel.check_regression(_record(0.48, 0.5), [_record(0.5, 0.5)]) == []


def test_gate_ignores_other_task_set_versions():
    """A grown golden set scores differently; that is not a regression."""
    history = [_record(0.9, 0.9, version="1.0")]
    assert flywheel.check_regression(_record(0.3, 0.3, version="1.1"), history) == []


def test_gate_compares_within_its_own_version():
    history = [_record(0.9, 0.9, version="1.0"), _record(0.5, 0.5, version="1.1")]
    assert flywheel.check_regression(_record(0.5, 0.5, version="1.1"), history) == []
    failures = flywheel.check_regression(_record(0.2, 0.5, version="1.1"), history)
    assert len(failures) == 1 and "coverage regressed" in failures[0]


def test_report_renders_latest_and_trend():
    history = [_record(0.4, 0.4, at="2026-01-01T00:00:00+00:00"),
               _record(0.6, 0.5, at="2026-01-02T00:00:00+00:00")]
    md = flywheel.render_report(history)
    assert "## Latest head-to-head" in md
    assert "## coverage over time" in md
    assert "2026-01-01T00:00:00+00:00" in md and "2026-01-02T00:00:00+00:00" in md
    assert "0.600" in md


def test_report_only_shows_current_task_set_version():
    history = [_record(0.9, 0.9, version="1.0", at="old"),
               _record(0.4, 0.4, version="1.1", at="new")]
    md = flywheel.render_report(history)
    assert "new" in md and "| old |" not in md
    assert "v1.1" in md


def test_report_states_run_conditions():
    keyless = _record(0.4, 0.4)
    keyless["conditions"] = {"live_search": False, "llm": False}
    md = flywheel.render_report([keyless])
    assert "store/cache only" in md and "heuristic fallbacks" in md

    live = _record(0.4, 0.4)
    live["conditions"] = {"live_search": True, "llm": True}
    assert "live crawl ON" in flywheel.render_report([live])


def test_report_renders_latency_without_false_precision():
    rec = _record(0.4, 0.4)
    rec["engines"]["moo"]["latency_ms"] = 2231.1
    assert "| 2,231 |" in flywheel.render_report([rec])


def test_report_with_no_history_is_still_valid():
    assert "No runs recorded yet" in flywheel.render_report([])


def test_write_report_creates_parent(tmp_path):
    path = flywheel.write_report([_record(0.5, 0.5)], tmp_path / "sub" / "eval-trends.md")
    assert path.exists() and "Latest head-to-head" in path.read_text(encoding="utf-8")
