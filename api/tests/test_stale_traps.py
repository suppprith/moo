"""The USP acceptance gate: stale-answer traps."""

import pytest

from app.eval import stale_traps
from app.eval.stale_traps import (
    engine_text,
    format_gate,
    format_transcript,
    gate,
    load_traps,
    run_traps,
    score_trap,
    structural_flags,
    summarize,
)

TRAP = {
    "id": "python-utcnow",
    "query": "how do I get the current UTC time in Python",
    "product": "python",
    "changed_in": "3.12",
    "change_type": "deprecated",
    "stale": {"summary": "utcnow is deprecated", "markers": ["utcnow()"]},
    "current": {"summary": "use an aware datetime", "markers": ["now(timezone.utc)"]},
    "authoritative_sources": ["docs.python.org"],
}
CUES = ["deprecated", "removed in", "no longer"]


def rows(*snippets):
    return {"results": [{"title": "t", "url": "https://docs.python.org/x", "snippet": s}
                        for s in snippets]}


def test_repeating_the_stale_answer_with_no_warning_fails():
    out = rows("Use datetime.utcnow() to get the current UTC time.")
    score = score_trap(out, TRAP, CUES)
    assert score["repeats_stale"] is True
    assert score["surfaces_current"] is False
    assert score["flags_stale"] is False
    assert score["silent_stale"] is True
    assert score["passes"] is False


def test_current_answer_without_naming_the_old_one_is_not_a_pass():
    out = rows("Call datetime.now(timezone.utc) for an aware timestamp.")
    score = score_trap(out, TRAP, CUES)
    assert score["surfaces_current"] is True
    assert score["flags_stale"] is False
    assert score["passes"] is False


def test_textual_flag_passes_for_any_engine():
    out = rows("datetime.utcnow() is deprecated; use datetime.now(timezone.utc) instead.")
    score = score_trap(out, TRAP, CUES)
    assert score["flags_stale"] is True
    assert score["flagged_structurally"] is False
    assert score["passes"] is True
    assert score["silent_stale"] is False


def test_structural_flag_counts_even_without_the_cue_words():
    report = {
        "executive_answer": "Use datetime.now(timezone.utc).",
        "findings": [{"text": "utcnow() returns a naive datetime", "superseded_by": "clm_9"}],
        "disputed_points": [{"text": "utcnow() is fine", "supports": [1], "contradicts": [2]}],
    }
    score = score_trap(report, TRAP, CUES)
    assert score["flagged_structurally"] is True
    assert score["passes"] is True
    assert score["two_sided"] is True
    assert score["signals"]["superseded"] == 1


def test_version_outdated_sources_are_a_structural_flag():
    out = {"results": [{"title": "t", "url": "u", "snippet": "now(timezone.utc)",
                        "version_outdated": True}]}
    assert structural_flags(out)["version_outdated_sources"] == 1
    assert score_trap(out, TRAP, CUES)["passes"] is True


def test_claims_ride_along_in_a_web_search_evidence_block():
    out = {"results": [{"snippet": "now(timezone.utc)"}],
           "evidence": {"claims": [{"text": "utcnow is naive", "disputed": True}]}}
    score = score_trap(out, TRAP, CUES)
    assert score["signals"]["disputed"] == 1
    assert score["passes"] is True


def test_an_errored_engine_scores_as_a_miss_not_a_gap():
    score = score_trap(None, TRAP, CUES)
    assert score["error"] is True and score["passes"] is False
    assert summarize([score])["errors"] == 1
    assert summarize([score])["pass_rate"] == 0.0


def test_engine_text_reads_every_shape():
    text = engine_text({
        "results": [{"title": "A", "snippet": "B", "highlights": ["C"]}],
        "answer": "D", "executive_answer": "E",
        "findings": [{"text": "F"}], "disputed_points": [{"text": "G"}],
        "claims": [{"text": "H"}],
    })
    assert set("abcdefgh") <= set(text.replace(" ", ""))


def test_quote_shows_the_matched_words():
    out = rows("some preamble then datetime.now(timezone.utc) and more text")
    assert "now(timezone.utc)" in score_trap(out, TRAP, CUES)["quote"]


def test_run_traps_scores_every_engine_over_every_trap():
    engines = {
        "moo_fast": lambda q: rows("datetime.utcnow() is deprecated, use now(timezone.utc)"),
        "competitor": lambda q: rows("Use datetime.utcnow()."),
        "broken": lambda q: (_ for _ in ()).throw(RuntimeError("boom")),
    }
    result = run_traps(None, [TRAP], engines, cues=CUES)
    assert result["n_traps"] == 1
    assert result["engines"]["moo_fast"]["summary"]["pass_rate"] == 1.0
    assert result["engines"]["competitor"]["summary"]["silent_stale_rate"] == 1.0
    assert result["engines"]["broken"]["summary"]["errors"] == 1


def _result(moo_rate, competitor_rate=None):
    engines = {"moo_deep": {"summary": {"pass_rate": moo_rate}, "rows": []}}
    if competitor_rate is not None:
        engines["tavily"] = {"summary": {"pass_rate": competitor_rate}, "rows": []}
    return {"engines": engines}


def test_gate_fails_below_the_bar():
    verdict = gate(_result(0.5))
    assert verdict["passed"] is False
    assert "bar 70%" in verdict["checks"][0]["detail"]


def test_gate_passes_on_the_bar_with_no_competitor_keys():
    verdict = gate(_result(0.7))
    assert verdict["passed"] is True
    assert verdict["checks"][1]["passed"] is None
    assert "not evaluated" in verdict["checks"][1]["detail"]


def test_gate_fails_when_a_competitor_keeps_up():
    verdict = gate(_result(0.8, competitor_rate=0.7))
    assert verdict["passed"] is False
    assert "USP wording" in verdict["checks"][1]["detail"]


def test_gate_passes_with_a_wide_margin():
    assert gate(_result(0.8, competitor_rate=0.1))["passed"] is True


def test_gate_uses_the_better_moo_engine():
    result = {"engines": {
        "moo_fast": {"summary": {"pass_rate": 0.2}, "rows": []},
        "moo_deep": {"summary": {"pass_rate": 0.9}, "rows": []},
    }}
    verdict = gate(result)
    assert verdict["passed"] is True
    assert "moo_deep" in verdict["checks"][0]["detail"]


def test_gate_reports_pass_and_fail_as_a_status():
    assert gate(_result(0.8))["status"] == "pass"
    assert gate(_result(0.3))["status"] == "fail"


def test_a_run_with_no_search_provider_is_not_a_failed_usp():
    """Every trap asks what the live web says today. With nothing fetched, 0%
    means the gate never ran — calling that a failure would cry wolf."""
    result = _result(0.0)
    result["config"] = {"engines": ["moo_fast"], "live_provider": None, "llm": False}
    verdict = gate(result)

    assert verdict["status"] == "not_evaluated"
    assert verdict["passed"] is False
    assert "nothing was fetched" in verdict["checks"][0]["detail"]
    assert "NOT EVALUATED" in format_gate(verdict)


def test_a_configured_provider_is_graded_normally():
    result = _result(0.8)
    result["config"] = {"engines": ["moo_fast"], "live_provider": "BraveProvider", "llm": True}
    assert gate(result)["status"] == "pass"


def test_transcript_records_what_each_engine_did():
    engines = {"moo_deep": lambda q: rows("utcnow() was removed in 3.12; use now(timezone.utc)"),
               "tavily": lambda q: rows("Use datetime.utcnow().")}
    result = run_traps(None, [TRAP], engines, cues=CUES)
    text = format_transcript(result, [TRAP], gate(result))
    assert TRAP["query"] in text
    assert "**moo_deep**: pass" in text
    assert "**tavily**: miss" in text
    assert "**no warning**" in text


@pytest.fixture(scope="module")
def data():
    return load_traps()


class TestTaskSet:
    """The committed traps are the artifact; keep them well-formed."""

    def test_size_and_versioning(self, data):
        assert 10 <= len(data["traps"]) <= 20
        assert data["version"] and data["verified_on"]
        assert data["staleness_cues"]

    def test_every_trap_is_complete(self, data):
        for trap in data["traps"]:
            for field in ("id", "query", "product", "changed_in", "change_type",
                          "authoritative_sources"):
                assert trap.get(field), f"{trap.get('id')} missing {field}"
            for side in ("stale", "current"):
                assert trap[side]["summary"]
                assert trap[side]["markers"], f"{trap['id']} has no {side} markers"
                assert all(m == m.lower() for m in trap[side]["markers"]), \
                    f"{trap['id']} {side} markers must be lowercase to match"

    def test_ids_are_unique(self, data):
        ids = [t["id"] for t in data["traps"]]
        assert len(ids) == len(set(ids))

    def test_stale_and_current_markers_do_not_overlap(self, data):
        for trap in data["traps"]:
            stale = set(trap["stale"]["markers"])
            current = set(trap["current"]["markers"])
            assert not (stale & current), f"{trap['id']} markers overlap"

    def test_the_stale_answer_alone_never_scores_a_pass(self, data):
        """The trap only works if quoting the outdated answer fails it."""
        for trap in data["traps"]:
            out = rows(" ".join(trap["stale"]["markers"]))
            score = score_trap(out, trap, data["staleness_cues"])
            assert score["passes"] is False, f"{trap['id']} passes on the stale answer alone"
            assert score["silent_stale"] is True

    def test_the_current_answer_with_a_warning_always_passes(self, data):
        for trap in data["traps"]:
            text = (f"{' '.join(trap['stale']['markers'])} is deprecated, use "
                    f"{' '.join(trap['current']['markers'])}")
            score = score_trap(rows(text), trap, data["staleness_cues"])
            assert score["passes"] is True, f"{trap['id']} cannot be passed"


def test_engine_selection_is_filterable(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    chosen = stale_traps.trap_engines(None, include=["moo_fast"])
    assert list(chosen) == ["moo_fast"]


def test_competitors_are_skipped_without_keys(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert set(stale_traps.trap_engines(None)) == {"moo_fast", "moo_deep"}
