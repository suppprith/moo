"""Unit tests for opaque handles."""

import pytest

from app import ids


def test_encode_decode_roundtrip():
    for kind in (ids.DOCUMENT, ids.CHUNK, ids.CLAIM, ids.ENTITY):
        handle = ids.encode(kind, 42)
        assert ids.decode(handle) == (kind, 42)


def test_encode_rejects_unknown_kind():
    with pytest.raises(ValueError):
        ids.encode("bogus", 1)


@pytest.mark.parametrize("bad", ["", "42", "xyz_1", "chk_", "chk_abc", "chk-1"])
def test_decode_rejects_malformed(bad):
    with pytest.raises(ValueError):
        ids.decode(bad)
