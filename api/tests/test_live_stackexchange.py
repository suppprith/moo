"""Stack Exchange questions fetched through the API, not scraped."""

import json

import pytest

from app.ingest.fetcher import FetchResult
from app.live import stackexchange as se


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(se, "_backoff_until", 0.0)
    monkeypatch.delenv("MOO_STACKEXCHANGE_KEY", raising=False)


class ApiFetcher:
    def __init__(self, questions, answers, extra=None):
        self.questions, self.answers, self.extra = questions, answers, extra or {}
        self.requests = []

    def get(self, url, **kw):
        self.requests.append(url)
        items = self.answers if "/answers?" in url else self.questions
        return FetchResult(url, 200, json.dumps({"items": items, **self.extra}), {})


def test_question_ref_recognizes_sites():
    assert se.question_ref("https://stackoverflow.com/questions/123/some-slug") == (
        "stackoverflow", 123)
    assert se.question_ref("https://www.stackoverflow.com/q/7") == ("stackoverflow", 7)
    assert se.question_ref("https://dba.stackexchange.com/questions/55/x") == ("dba", 55)
    assert se.question_ref("https://askubuntu.com/questions/9/x") == ("askubuntu", 9)


def test_question_ref_ignores_non_questions():
    assert se.question_ref("https://stackoverflow.com/a/123") is None
    assert se.question_ref("https://stackoverflow.com/tags/python") is None
    assert se.question_ref("https://meta.stackexchange.com/questions/1/x") is None
    assert se.question_ref("https://docs.python.org/3/questions/1") is None


def test_body_keeps_code_verbatim():
    body = ("<p>Use <code>datetime.now(timezone.utc)</code> &amp; not utcnow.</p>"
            "<pre><code>from datetime import timezone\nnow = datetime.now(timezone.utc)\n"
            "</code></pre>")
    md = se.body_to_markdown(body)
    assert "`datetime.now(timezone.utc)`" in md
    assert "& not utcnow" in md
    assert "```\nfrom datetime import timezone\nnow = datetime.now(timezone.utc)\n```" in md


def test_one_site_costs_two_requests_however_many_questions():
    questions = [{"question_id": q, "title": f"Q{q}", "body": "<p>q</p>",
                  "link": f"https://stackoverflow.com/questions/{q}", "score": 1}
                 for q in (1, 2, 3)]
    fetcher = ApiFetcher(questions, [])
    docs = se.fetch_questions(fetcher, "stackoverflow", [1, 2, 3])
    assert set(docs) == {1, 2, 3}
    assert len(fetcher.requests) == 2
    assert "/questions/1;2;3?" in fetcher.requests[0]


def test_answers_in_vote_order_with_date_and_acceptance():
    questions = [{"question_id": 1, "title": "UTC now?", "body": "<p>how</p>",
                  "link": "https://stackoverflow.com/questions/1", "score": 5}]
    answers = [
        {"question_id": 1, "score": 900, "is_accepted": True, "creation_date": 1262304000,
         "body": "<p>Use <code>datetime.utcnow()</code></p>"},
        {"question_id": 1, "score": 120, "creation_date": 1704067200,
         "body": "<p>utcnow is deprecated since 3.12; use <code>datetime.now(timezone.utc)</code></p>"},
    ]
    doc = se.fetch_questions(ApiFetcher(questions, answers), "stackoverflow", [1],
                             urls={1: "https://stackoverflow.com/questions/1/utc-now"})[1]
    assert doc.url == "https://stackoverflow.com/questions/1/utc-now"
    assert doc.source_type == "so_answer"
    old, new = doc.text.index("utcnow()"), doc.text.index("now(timezone.utc)")
    assert old < new
    assert "## Answer (score 900, accepted, 2010-01-01)" in doc.text
    assert "## Answer (score 120, 2024-01-01)" in doc.text


def test_backoff_pauses_the_api(monkeypatch):
    fetcher = ApiFetcher([{"question_id": 1, "title": "t", "link": "u"}], [],
                         extra={"backoff": 30})
    se.fetch_questions(fetcher, "stackoverflow", [1])
    before = len(fetcher.requests)
    assert se.fetch_questions(fetcher, "stackoverflow", [1]) == {}
    assert len(fetcher.requests) == before


def test_api_error_returns_nothing():
    class Down:
        def get(self, url, **kw):
            return FetchResult(url, 0, "", {}, ok=False, error="boom")

    assert se.fetch_questions(Down(), "stackoverflow", [1]) == {}
