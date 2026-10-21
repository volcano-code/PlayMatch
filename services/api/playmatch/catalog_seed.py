"""Load a checksummed, source-linked catalog release without external requests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from playmatch.schemas import Game

DATA = Path(__file__).resolve().parents[3] / "data"


def validate_catalog(games: Any) -> list[dict[str, Any]]:
    if not isinstance(games, list) or not 1 <= len(games) <= 5000:
        raise ValueError("catalog must contain 1..5000 games")
    ids, apps = set(), set()
    for game in games:
        parsed = Game.model_validate(game)
        if set(game) - set(Game.model_fields):
            raise ValueError("Unexpected catalog field; prices must not be embedded")
        if parsed.id in ids or parsed.steam_app_id in apps:
            raise ValueError("Duplicate game identity or Steam app")
        if parsed.id != f"steam-{parsed.steam_app_id}" or parsed.steam_app_id <= 0:
            raise ValueError("Invalid curated game identity")
        if parsed.store_url != f"https://store.steampowered.com/app/{parsed.steam_app_id}/":
            raise ValueError("Catalog identity and store URL disagree")
        if parsed.metadata_status != "curated" or parsed.estimated_hours is not None:
            raise ValueError("Bundled games must be curated; playtime is unverified")
        for field in ("chinese_support", "coop", "horror"):
            if getattr(parsed, field) != "unknown" and not any(
                evidence.field == field
                and evidence.observed_at
                and evidence.url == parsed.store_url
                for evidence in parsed.evidence
            ):
                raise ValueError(f"Missing field evidence: {field}")
        ids.add(parsed.id)
        apps.add(parsed.steam_app_id)
    # Keep legacy bytes/shape unchanged for the fixed historical evaluation.
    return games


def load_release(directory: Path = DATA) -> list[dict[str, Any]]:
    manifest = json.loads((directory / "catalog-release.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("parts"), list):
        raise ValueError("Invalid release manifest")
    games, seen_paths = [], set()
    for part in manifest["parts"]:
        name = part["path"]
        if not isinstance(name, str) or Path(name).name != name or name in seen_paths:
            raise ValueError("Invalid or duplicate release path")
        seen_paths.add(name)
        body = (directory / name).read_bytes()
        if hashlib.sha256(body).hexdigest() != part["sha256"]:
            raise ValueError("Catalog release checksum mismatch")
        rows = json.loads(body)
        if not isinstance(rows, list) or len(rows) != part["count"]:
            raise ValueError("Catalog release count mismatch")
        games.extend(rows)
    if len(games) != manifest.get("total_games"):
        raise ValueError("Catalog release total mismatch")
    return validate_catalog(games)


def load_catalog() -> list[dict[str, Any]]:
    """An explicit PLAYMATCH_CATALOG_PATH replaces, never appends to, the release."""
    configured = os.getenv("PLAYMATCH_CATALOG_PATH")
    if configured:
        return validate_catalog(json.loads(Path(configured).read_text(encoding="utf-8")))
    return load_release()


def catalog_coverage(games: list[dict]) -> dict:
    """Count actual stored facts, not configured seed size or inferred capability."""

    def observed(game: dict, field: str) -> bool:
        return any(e.get("field") == field and e.get("observed_at") for e in game["evidence"])

    return {
        "total_games": len(games),
        "source_linked_games": sum(bool(g["evidence"]) for g in games),
        "description_observed": sum(observed(g, "description") for g in games),
        "chinese_interface_verified": sum(
            g["chinese_support"] == "verified" and observed(g, "chinese_support") for g in games
        ),
        "online_coop_verified": sum(g["coop"] == "online" and observed(g, "coop") for g in games),
        "local_coop_verified": sum(g["coop"] == "local" and observed(g, "coop") for g in games),
        "horror_unknown": sum(g["horror"] == "unknown" for g in games),
        "with_aliases": sum(bool(g.get("aliases")) for g in games),
        "automatic_sync": False,
        "notes": [
            "覆盖统计不等于全部属性核验，也不代表最低购买价。",
            "简介与别名包含开发助手整理内容，不是独立玩家评测。",
        ],
    }
