"""Contract-drift guard: the SDK must cover every endpoint moo publishes.

Mirrors api/tests/test_openapi.py, from the client side: when the API grows an
endpoint, this fails until the SDK maps it to a method.
"""

import json
from pathlib import Path

import pytest

from moo import AsyncMoo, Moo
from moo._ops import ENDPOINTS

SPEC = Path(__file__).resolve().parents[3] / "api" / "openapi.json"


def published_operations():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    return {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method.lower() in ("get", "post", "put", "patch", "delete")
    }


@pytest.mark.skipif(not SPEC.exists(), reason="openapi.json not present")
def test_every_published_endpoint_has_a_client_method():
    missing = published_operations() - set(ENDPOINTS)
    assert not missing, f"SDK is missing methods for: {sorted(missing)}"


@pytest.mark.skipif(not SPEC.exists(), reason="openapi.json not present")
def test_no_stale_endpoints_mapped():
    stale = set(ENDPOINTS) - published_operations()
    assert not stale, f"SDK maps endpoints the API no longer publishes: {sorted(stale)}"


@pytest.mark.parametrize("name", sorted(set(ENDPOINTS.values())))
def test_mapped_methods_exist_on_both_clients(name):
    assert callable(getattr(Moo, name, None)), f"Moo.{name} is missing"
    assert callable(getattr(AsyncMoo, name, None)), f"AsyncMoo.{name} is missing"
