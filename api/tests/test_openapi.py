"""Machine-readable contract: OpenAPI + /contract descriptor."""

import json

from fastapi.testclient import TestClient

from app.export_openapi import SPEC_PATH, build_spec
from app.main import app


def test_committed_openapi_is_in_sync():
    assert SPEC_PATH.exists(), "run: uv run python -m app.export_openapi"
    committed = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    assert committed == build_spec(), (
        "OpenAPI drift — regenerate with: uv run python -m app.export_openapi"
    )


def test_spec_covers_the_agent_endpoints():
    paths = build_spec()["paths"]
    for p in ("/search", "/research", "/v1/web_search", "/source/{id}", "/health", "/contract"):
        assert p in paths


def test_spec_params_are_strict_enough_for_tool_gen():
    spec = build_spec()
    params = spec["paths"]["/search"]["get"]["parameters"]
    by_name = {p["name"]: p for p in params}
    assert set(by_name["mode"]["schema"]["enum"]) == {"raw", "claims", "full"}
    assert set(by_name["format"]["schema"]["enum"]) == {"full", "agent"}
    assert by_name["q"]["required"] is True


def test_contract_descriptor_exposes_version_and_tool_schemas():
    client = TestClient(app)
    r = client.get("/contract")
    assert r.status_code == 200
    body = r.json()
    assert body["contract_version"] and body["openapi"] == "/openapi.json"
    assert body["handle_formats"]["claim"] == "clm_<id>"
    names = {t["name"] for t in body["mcp_tools"]}
    assert {"search", "deep_research", "fetch_source"} <= names
    assert all("input_schema" in t for t in body["mcp_tools"])
