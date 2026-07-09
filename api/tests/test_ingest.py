"""Unit tests for ingestion primitives (fetcher result, models, github role)."""

from app.ingest.fetcher import FetchResult
from app.ingest.github import _role
from app.ingest.models import RawDoc

# -- FetchResult header handling (regression: Link pagination bug) --------------

def test_headers_normalized_to_lowercase():
    r = FetchResult("http://x", 200, "", {"Link": "<http://n>; rel=\"next\"", "ETag": "e"})
    assert r.headers == {"link": '<http://n>; rel="next"', "etag": "e"}


def test_header_lookup_case_insensitive():
    r = FetchResult("http://x", 200, "", {"Content-Type": "text/html"})
    assert r.header("content-type") == "text/html"
    assert r.header("CONTENT-TYPE") == "text/html"
    assert r.header("missing") == ""
    assert r.header("missing", "dflt") == "dflt"


def test_json_property():
    r = FetchResult("http://x", 200, '{"a": 1}', {})
    assert r.json == {"a": 1}
    assert FetchResult("http://x", 200, "", {}).json is None


# -- RawDoc.content_hash ---------------------------------------------------------

def test_content_hash_stable_and_content_sensitive():
    a = RawDoc(source_type="docs", url="u", title="t", text="body")
    b = RawDoc(source_type="docs", url="u", title="t", text="body")
    c = RawDoc(source_type="docs", url="u", title="t", text="different")
    assert a.content_hash() == b.content_hash()
    assert a.content_hash() != c.content_hash()


def test_content_hash_ignores_volatile_fields():
    a = RawDoc(source_type="so_answer", url="u", title="t", text="body", popularity=5)
    b = RawDoc(source_type="so_answer", url="u", title="t", text="body", popularity=999)
    assert a.content_hash() == b.content_hash()  # votes don't churn the corpus


# -- GitHub author_association mapping -------------------------------------------

def test_role_mapping():
    assert _role("OWNER") == "maintainer"
    assert _role("MEMBER") == "maintainer"
    assert _role("COLLABORATOR") == "maintainer"
    assert _role("CONTRIBUTOR") == "contributor"
    assert _role("NONE") == "none"
    assert _role(None) == "none"
    assert _role("weird-future-value") == "none"
