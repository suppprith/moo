"""Export the OpenAPI spec to a checked-in file.

The spec is generated from the FastAPI app and committed to ``api/openapi.json``
so it is reviewable and diffable. ``tests/test_openapi.py`` fails if the app and
the committed spec drift — regenerate with this command:

    uv run python -m app.export_openapi
"""

from __future__ import annotations

import json
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def build_spec() -> dict:
    from .main import app

    return app.openapi()


def main() -> int:
    SPEC_PATH.write_text(json.dumps(build_spec(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {SPEC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
