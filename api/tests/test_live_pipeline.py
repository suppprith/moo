"""End-to-end live pipeline over a temp DB (app.live.pipeline, SUP-130).

Provider + fetcher are stubbed (no network); everything downstream — document
upsert, chunking, embedding, vec/FTS index, hybrid retrieval — is real.
"""

import pytest

from app.db import migrate
from app.index import vector
from app.ingest.fetcher import FetchResult
from app.live.pipeline import live_fetch
from app.live.providers import Candidate, Provider
from app.retrieve import retrieve

PG_URL = "https://www.postgresql.org/docs/16/routine-vacuuming.html"
SO_URL = "https://stackoverflow.com/questions/12345/when-does-autovacuum-run"

PG_HTML = """<html><head><title>Routine Vacuuming</title></head><body>
<article>
<h1>Routine Vacuuming</h1>
<p>PostgreSQL databases require periodic maintenance known as vacuuming.
The VACUUM command reclaims storage occupied by dead tuples, because in normal
PostgreSQL operation tuples that are deleted or obsoleted by an update are not
physically removed from their table; they remain present until a vacuum is done.</p>
<h2>Autovacuum</h2>
<p>PostgreSQL has an optional but highly recommended feature called autovacuum,
whose purpose is to automate the execution of VACUUM and ANALYZE commands.
When enabled, autovacuum checks for tables that have had a large number of
inserted, updated or deleted tuples and reclaims space automatically.</p>
</article></body></html>"""

SO_HTML = """<html><head><title>When does autovacuum run?</title></head><body>
<article>
<h1>When does autovacuum actually run?</h1>
<p>Autovacuum triggers when the number of dead tuples exceeds a threshold
computed from autovacuum_vacuum_threshold plus autovacuum_vacuum_scale_factor
times the number of tuples in the table. The default scale factor is 0.2 so a
table must churn twenty percent of its rows before the autovacuum daemon
considers it, which is why large tables can bloat before vacuum kicks in.</p>
</article></body></html>"""

PAGES = {PG_URL: PG_HTML, SO_URL: SO_HTML}


class FakeProvider(Provider):
    name = "fake"

    def __init__(self, cands):
        self._cands = cands

    def discover(self, query, *, count=12):
        return self._cands[:count]


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def get(self, url, **kw):
        self.requests.append(url)
        html = self.pages.get(url)
        if html is None:
            return FetchResult(url, 404, "", {}, ok=False, error="HTTP 404")
        return FetchResult(url, 200, html, {"Content-Type": "text/html"})


DEV_CANDS = [
    Candidate(PG_URL, title="Routine Vacuuming", rank=0),
    Candidate(SO_URL, title="When does autovacuum run?", rank=1),
]

FOOD_CANDS = [
    Candidate("https://tasty.example.com/pizza", rank=0),
    Candidate("https://recipes.example.org/dough", rank=1),
    Candidate("https://food.example.net/best-oven", rank=2),
]


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "live.sqlite"
    migrate(db)
    c = vector.connect(db)
    yield c
    c.close()


def test_unconfigured_is_noop(conn):
    report = live_fetch(conn, "postgres vacuum", provider=None, fetcher=FakeFetcher({}))
    assert report["available"] is False
    assert conn.execute("SELECT count(*) FROM document").fetchone()[0] == 0


def test_live_fetch_end_to_end(conn):
    fetcher = FakeFetcher(PAGES)
    report = live_fetch(conn, "when does postgres autovacuum run",
                        provider=FakeProvider(DEV_CANDS), fetcher=fetcher)

    assert report["available"] and report["provider"] == "fake"
    assert not report["out_of_domain"]
    assert report["fetched"] == 2 and report["failed"] == 0
    assert len(report["new_docs"]) == 2 and report["new_chunks"] > 0
    assert report["embedded"] == report["new_chunks"]

    # docs landed with policy-derived source types + live marker
    types = {r["source_type"] for r in conn.execute("SELECT source_type FROM document")}
    assert types == {"docs", "so"}
    # vec + fts indexes were synced
    assert conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] == report["new_chunks"]

    # the freshly fetched content is immediately retrievable via the normal path
    hits = retrieve(conn, "when does postgres autovacuum run", k=4)
    assert hits and any("autovacuum" in h.text.lower() for h in hits)


def test_second_run_is_warm(conn):
    fetcher = FakeFetcher(PAGES)
    live_fetch(conn, "postgres autovacuum", provider=FakeProvider(DEV_CANDS), fetcher=fetcher)
    report2 = live_fetch(conn, "postgres autovacuum",
                         provider=FakeProvider(DEV_CANDS), fetcher=fetcher)
    # unchanged content -> no new docs, no rechunk/re-embed work
    assert report2["unchanged"] == 2 and report2["fetched"] == 0
    assert report2["new_chunks"] == 0 and report2["embedded"] == 0
    assert conn.execute("SELECT count(*) FROM document").fetchone()[0] == 2


def test_updated_page_is_rechunked(conn):
    fetcher = FakeFetcher(dict(PAGES))
    live_fetch(conn, "postgres autovacuum", provider=FakeProvider(DEV_CANDS), fetcher=fetcher)
    fetcher.pages[PG_URL] = PG_HTML.replace(
        "highly recommended feature", "highly recommended background daemon"
    )
    report = live_fetch(conn, "postgres autovacuum",
                        provider=FakeProvider(DEV_CANDS), fetcher=fetcher)
    assert len(report["updated_docs"]) == 1
    assert report["new_chunks"] > 0  # updated doc got fresh chunks
    text = conn.execute(
        "SELECT group_concat(text) FROM chunk JOIN document d ON d.id = document_id "
        "WHERE d.url = ?", (PG_URL,),
    ).fetchone()[0]
    assert "background daemon" in text


def test_out_of_domain_skips_fetch(conn):
    fetcher = FakeFetcher({})
    report = live_fetch(conn, "best pizza dough recipe",
                        provider=FakeProvider(FOOD_CANDS), fetcher=fetcher)
    assert report["out_of_domain"] is True
    assert fetcher.requests == []  # nothing fetched
    assert conn.execute("SELECT count(*) FROM document").fetchone()[0] == 0


def test_failed_pages_degrade_gracefully(conn):
    cands = DEV_CANDS + [Candidate("https://docs.python.org/3/missing.html", rank=2)]
    report = live_fetch(conn, "postgres autovacuum settings",
                        provider=FakeProvider(cands), fetcher=FakeFetcher(PAGES))
    assert report["failed"] == 1
    assert report["fetched"] == 2  # the good pages still landed
