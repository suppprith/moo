"""Deep-research benchmark scoring + runner."""

from app.eval import benchmark as bench


def test_task_set_loads_and_is_versioned():
    data = bench.load_tasks()
    assert data["version"] and data["tasks"]
    for t in data["tasks"]:
        assert {"id", "question", "rubric_points"} <= set(t)


def test_score_coverage():
    report = {"executive_answer": "Postgres beats MySQL on joins",
              "findings": [{"text": "use an index"}]}
    cov = bench.score_coverage(report, ["postgres", "mysql", "join", "index", "query"])
    assert cov == 0.8


def test_score_coverage_none_when_no_rubric():
    assert bench.score_coverage({"executive_answer": "x"}, []) is None


def test_source_recall():
    report = {"sources": [{"url": "https://www.postgresql.org/docs"}, {"url": "https://news.ycombinator.com/x"}]}
    assert bench.score_source_recall(report, ["postgresql.org", "dev.mysql.com"]) == 0.5


def test_contradiction_recall():
    with_dispute = {"disputed_points": [{"text": "sources disagree on performance"}]}
    assert bench.score_contradiction_recall(with_dispute, ["performance"]) == 1.0
    assert bench.score_contradiction_recall({"disputed_points": []}, ["performance"]) == 0.0
    assert bench.score_contradiction_recall({"disputed_points": []}, []) is None


def test_citation_accuracy_from_groundedness():
    assert bench.citation_accuracy({"groundedness": {"pct_grounded": 0.9}}) == 0.9
    assert bench.citation_accuracy({}) is None


def test_search_as_report_adapter():
    sr = {"answer": "A", "claims": [{"text": "c1", "disputed": True}, {"text": "c2", "disputed": False}],
          "sources": [{"url": "u"}]}
    rep = bench._search_as_report(sr)
    assert rep["executive_answer"] == "A"
    assert len(rep["findings"]) == 2 and len(rep["disputed_points"]) == 1


def test_run_benchmark_aggregates(monkeypatch):
    monkeypatch.setattr("app.research.session.run_research",
                        lambda conn, q, **k: {"run_id": "r", "status": "done",
                                              "budget": {"steps_used": 3, "elapsed_ms": 1000}})
    monkeypatch.setattr("app.research.session.delete_run", lambda *a, **k: True)
    monkeypatch.setattr("app.research.report.assemble_report",
                        lambda conn, run, **k: {"executive_answer": "postgres mysql join",
                                                "findings": [{"text": "postgres mysql join"}],
                                                "sources": [{"url": "https://postgresql.org"}],
                                                "disputed_points": [{"text": "faster disagreement"}],
                                                "groundedness": {"pct_grounded": 1.0}})
    monkeypatch.setattr("app.search.search",
                        lambda conn, q, **k: {"answer": "postgres", "claims": [{"text": "postgres", "disputed": False}],
                                              "sources": [{"url": "https://x"}]})
    tasks = [{"id": "t1", "question": "q", "rubric_points": ["postgres", "mysql", "join"],
              "key_source_hints": ["postgresql.org"], "disputed_hints": ["faster"]}]
    result = bench.run_benchmark(None, tasks, use_llm=False)
    assert result["n_tasks"] == 1
    dr = result["summary"]["deep_research"]
    assert dr["coverage"] == 1.0 and dr["citation_accuracy"] == 1.0
    assert dr["source_recall"] == 1.0 and dr["contradiction_recall"] == 1.0
    assert result["summary"]["search"]["coverage"] < dr["coverage"]
    assert "deep_research" in bench.format_table(result)
