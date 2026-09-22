"""Live-fetch concurrency, deadline and cost accounting."""

import threading
import time

import pytest

from app.db import migrate
from app.index import vector
from app.ingest.fetcher import FetchResult
from app.live.pipeline import live_fetch
from app.live.providers import Candidate
from tests.test_live_pipeline import FakeProvider

_HTML = """<html><head><title>{title}</title></head><body><article>
<h1>{title}</h1>
<p>PostgreSQL vacuum reclaims storage occupied by dead tuples because deleted
or updated rows are not physically removed until a vacuum runs. The autovacuum
daemon automates this maintenance based on table churn thresholds and the
configured scale factor, which defaults to twenty percent of the table.</p>
</article></body></html>"""


def _page(title: str) -> str:
    return _HTML.format(title=title)


class SlowFetcher:
    """Thread-recording fetcher: sleeps per request, logs (url, start, end, thread)."""

    def __init__(self, pages: dict[str, str], delay: float):
        self.pages = pages
        self.delay = delay
        self.log: list[tuple[str, float, float, int]] = []
        self._lock = threading.Lock()

    def get(self, url, **kw):
        start = time.monotonic()
        time.sleep(self.delay)
        end = time.monotonic()
        with self._lock:
            self.log.append((url, start, end, threading.get_ident()))
        html = self.pages.get(url)
        if html is None:
            return FetchResult(url, 404, "", {}, ok=False, error="HTTP 404")
        return FetchResult(url, 200, html, {"Content-Type": "text/html"})


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "live_perf.sqlite"
    migrate(db)
    c = vector.connect(db)
    yield c
    c.close()


MULTI_HOST_PAGES = {
    "https://docs.python.org/3/vacuum.html": _page("A"),
    "https://www.postgresql.org/docs/16/vacuum.html": _page("B"),
    "https://kubernetes.io/docs/vacuum/": _page("C"),
    "https://redis.io/docs/persistence/": _page("D"),
}
MULTI_HOST_CANDS = [Candidate(u, rank=i) for i, u in enumerate(MULTI_HOST_PAGES)]

SAME_HOST_PAGES = {
    f"https://docs.python.org/3/page{i}.html": _page(f"P{i}") for i in range(4)
}
SAME_HOST_CANDS = [Candidate(u, rank=i) for i, u in enumerate(SAME_HOST_PAGES)]


def test_hosts_fetch_concurrently(conn):
    delay = 0.25
    fetcher = SlowFetcher(MULTI_HOST_PAGES, delay)
    t0 = time.monotonic()
    report = live_fetch(conn, "postgres vacuum behavior",
                        provider=FakeProvider(MULTI_HOST_CANDS), fetcher=fetcher)
    fetch_elapsed = report["timings_ms"]["fetch"] / 1000
    assert report["fetched"] == 4 and report["timed_out"] == 0
    assert fetch_elapsed < 3 * delay, f"fetch not concurrent: {fetch_elapsed:.2f}s"
    assert time.monotonic() - t0 < 30


def test_same_host_stays_serial(conn):
    fetcher = SlowFetcher(SAME_HOST_PAGES, 0.05)
    live_fetch(conn, "python docs pages",
               provider=FakeProvider(SAME_HOST_CANDS), fetcher=fetcher)
    spans = sorted((s, e) for _, s, e, _ in fetcher.log)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:], strict=False):
        assert e1 <= s2 + 1e-6, "same-host fetches overlapped"
    assert len(fetcher.log) == 4


def test_deadline_returns_partial_results(conn):
    delay = 0.4
    fetcher = SlowFetcher(SAME_HOST_PAGES, delay)
    t0 = time.monotonic()
    report = live_fetch(conn, "python docs pages", max_seconds=0.6,
                        provider=FakeProvider(SAME_HOST_CANDS), fetcher=fetcher)
    elapsed = time.monotonic() - t0
    assert report["timed_out"] > 0
    assert report["fetched"] >= 1
    assert report["fetched"] + report["timed_out"] + report["failed"] == 4
    assert elapsed < 4 * delay + 8


def test_cost_block_accounts_provider_and_pages(conn):
    fetcher = SlowFetcher(MULTI_HOST_PAGES, 0.0)
    report = live_fetch(conn, "postgres vacuum",
                        provider=FakeProvider(MULTI_HOST_CANDS), fetcher=fetcher)
    assert report["cost"]["provider_calls"] == 0
    assert report["cost"]["pages_attempted"] == 4
    assert report["cost"]["max_seconds"] > 0


def test_warm_repeat_is_faster_than_cold(conn):
    delay = 0.15
    fetcher = SlowFetcher(MULTI_HOST_PAGES, delay)
    cold = live_fetch(conn, "postgres vacuum",
                      provider=FakeProvider(MULTI_HOST_CANDS), fetcher=fetcher)
    warm = live_fetch(conn, "postgres vacuum",
                      provider=FakeProvider(MULTI_HOST_CANDS), fetcher=fetcher)
    assert warm["fresh"] == 4 and warm["new_chunks"] == 0
    assert len(fetcher.log) == 4
    assert "embed" not in warm["timings_ms"]
    assert cold["new_chunks"] > 0
