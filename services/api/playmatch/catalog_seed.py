"""Load the reviewed, source-linked starter catalog without external requests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_catalog() -> list[dict[str, Any]]:
    """Return fresh dictionaries so request code cannot mutate global seed data.

    Source deployments locate the JSON relative to this module. Container and
    wheel deployments can set PLAYMATCH_CATALOG_PATH to their mounted catalog.
    """
    configured = os.getenv("PLAYMATCH_CATALOG_PATH")
    path = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[3] / "data" / "catalog.json"
    )
    with path.open(encoding="utf-8") as handle:
        games = json.load(handle)
    if not isinstance(games, list) or not games:
        raise ValueError("catalog must be a non-empty list")
    seen: set[str] = set()
    for game in games:
        if not isinstance(game, dict) or not isinstance(game.get("id"), str):
            raise ValueError("catalog game must have a string id")
        if game["id"] in seen:
            raise ValueError("catalog contains duplicate game ids")
        seen.add(game["id"])
        if game.get("metadata_status") != "curated":
            raise ValueError("starter catalog records must be marked curated")
    return games
