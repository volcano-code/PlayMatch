"""Reject a stale checked-in API specification without making network calls."""

import json
from pathlib import Path

from playmatch.main import app


def main() -> None:
    path = Path(__file__).resolve().parents[1] / "packages/contracts/openapi.json"
    actual = app.openapi()
    expected = json.loads(path.read_text(encoding="utf-8"))
    if expected != actual:
        raise SystemExit(
            "OpenAPI differs from packages/contracts/openapi.json. "
            "Export the schema after updating the API."
        )
    print(f"OpenAPI is current: {len(actual['paths'])} paths")


if __name__ == "__main__":
    main()
