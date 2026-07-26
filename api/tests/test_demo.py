"""Agent demo transcript."""

import asyncio

from app.eval import demo


def test_run_demo_builds_full_transcript(monkeypatch):
    monkeypatch.setattr("app.retrieve.retrieve", lambda conn, q, **k: [])
    monkeypatch.setattr("app.index.vector.connect", lambda: object())

    async def fake_deep_research(question, **k):
        return {
            "run_id": "abc123def456ff", "status": "partial",
            "groundedness": {"findings_grounded": 2, "findings_total": 2,
                             "well_supported": 1, "answer_citations_valid": True},
            "executive_answer": "Postgres wins on joins [S1]",
            "findings": [{"text": "PG handles joins well", "confidence": 0.8,
                          "disputed": False, "citations": [1], "claim": "clm_1"}],
            "disputed_points": [{"text": "they disagree", "supports": [1], "contradicts": [2]}],
            "open_questions": ["what about scale?"],
            "sources": [{"handle": "doc_1"}],
        }

    monkeypatch.setattr("app.mcp_server.deep_research", fake_deep_research)
    monkeypatch.setattr("app.mcp_server.get_claim", lambda h: {
        "confidence": 0.8, "disputed": True,
        "evidence": [{"relation": "supports", "source": "chk_1", "trust_score": 0.9, "excerpt": "x"}]})
    monkeypatch.setattr("app.mcp_server.fetch_source", lambda h, **k: {"title": "T", "url": "u", "chunks": []})
    monkeypatch.setattr("app.mcp_server.list_contradictions", lambda q, **k: {"contradictions": [{"text": "c"}]})

    transcript = asyncio.run(demo.run_demo("Postgres vs MySQL", max_steps=1))
    assert "# moo agent demo" in transcript
    for section in ("deep_research", "get_claim", "fetch_source", "list_contradictions"):
        assert section in transcript
    assert "clm_1" in transcript and "grounded **2/2**" in transcript
    assert "Disputed point" in transcript
