"""Temporal claim validity + supersession (app.evidence.temporal, SUP-137)."""

import pytest

from app.db import migrate
from app.evidence import temporal
from app.index import vector

OLD_TEXT = ("In PostgreSQL 9.4 the vacuum process runs single-threaded, "
            "so large tables are cleaned by one worker only.")
NEW_TEXT = ("Since PostgreSQL 13 the vacuum process runs in parallel, "
            "so large tables are cleaned by multiple workers at once.")
UNRELATED = ("Redis 7 introduced sharded pub/sub channels for cluster mode "
             "so message fan-out no longer floods every node.")
UNVERSIONED = "Indexes generally speed up reads at the cost of slower writes."


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "temporal.sqlite"
    migrate(db)
    c = vector.connect(db)
    yield c
    c.close()


def _mk_claim(conn, text, disputed=0):
    cur = conn.execute(
        "INSERT INTO claim (text, normalized_key, confidence, disputed) VALUES (?, ?, 0.6, ?)",
        (text, text[:60], disputed),
    )
    conn.commit()
    return cur.lastrowid


# -- annotation ---------------------------------------------------------------------

def test_annotate_since_sets_valid_from(conn):
    cid = _mk_claim(conn, NEW_TEXT)
    n = temporal.annotate_versions(conn, [{"id": cid, "text": NEW_TEXT}])
    assert n == 1
    row = conn.execute("SELECT valid_product, valid_from FROM claim WHERE id=?", (cid,)).fetchone()
    assert row["valid_product"] == "postgresql" and row["valid_from"] == "13"


def test_annotate_removed_sets_valid_until(conn):
    text = "The distutils module was removed in Python 3.12 after a long deprecation."
    cid = _mk_claim(conn, text)
    temporal.annotate_versions(conn, [{"id": cid, "text": text}])
    row = conn.execute("SELECT valid_product, valid_until FROM claim WHERE id=?", (cid,)).fetchone()
    assert row["valid_product"] == "python" and row["valid_until"] == "3.12"


def test_annotate_skips_unversioned(conn):
    cid = _mk_claim(conn, UNVERSIONED)
    assert temporal.annotate_versions(conn, [{"id": cid, "text": UNVERSIONED}]) == 0


# -- supersession -------------------------------------------------------------------

def test_version_chain_supersedes_and_undisputes(conn):
    old_id = _mk_claim(conn, OLD_TEXT, disputed=1)   # falsely "disputed" by the new claim
    new_id = _mk_claim(conn, NEW_TEXT, disputed=0)
    claims = [{"id": old_id, "text": OLD_TEXT}, {"id": new_id, "text": NEW_TEXT}]
    result = temporal.process(conn, claims)
    assert result["superseded"] == 1

    link = conn.execute("SELECT * FROM claim_link WHERE relation='supersedes'").fetchone()
    assert link["claim_id"] == new_id and link["target_claim_id"] == old_id
    # the old claim is history, not controversy
    assert conn.execute("SELECT disputed FROM claim WHERE id=?", (old_id,)).fetchone()[0] == 0
    assert temporal.superseded_by(conn, [old_id]) == {old_id: new_id}


def test_different_subjects_not_linked(conn):
    a = _mk_claim(conn, OLD_TEXT)
    b = _mk_claim(conn, UNRELATED)  # different product AND different subject
    temporal.process(conn, [{"id": a, "text": OLD_TEXT}, {"id": b, "text": UNRELATED}])
    assert conn.execute("SELECT count(*) FROM claim_link").fetchone()[0] == 0


def test_same_version_era_stays_disputed(conn):
    # two claims about the same subject AND same version: a live dispute
    t1 = "In PostgreSQL 13 parallel vacuum improves cleanup speed on large tables."
    t2 = "In PostgreSQL 13 parallel vacuum rarely improves cleanup speed on large tables."
    a = _mk_claim(conn, t1, disputed=1)
    b = _mk_claim(conn, t2, disputed=1)
    temporal.process(conn, [{"id": a, "text": t1}, {"id": b, "text": t2}])
    assert conn.execute("SELECT count(*) FROM claim_link").fetchone()[0] == 0
    assert conn.execute("SELECT disputed FROM claim WHERE id=?", (a,)).fetchone()[0] == 1


# -- payload surfacing ---------------------------------------------------------------

def test_claims_payload_carries_temporal_fields(conn):
    from app.search import _claims_payload

    old_id = _mk_claim(conn, OLD_TEXT, disputed=1)
    new_id = _mk_claim(conn, NEW_TEXT)
    temporal.process(conn, [{"id": old_id, "text": OLD_TEXT}, {"id": new_id, "text": NEW_TEXT}])
    payload = {c["id"]: c for c in _claims_payload(conn, [old_id, new_id])}
    assert payload[old_id]["superseded_by"] == new_id
    assert payload[old_id]["disputed"] is False
    assert payload[new_id]["valid"] == {"product": "postgresql", "from": "13", "until": None}
