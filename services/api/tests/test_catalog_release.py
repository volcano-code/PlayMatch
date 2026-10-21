import hashlib
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from playmatch.catalog_seed import (
    DATA,
    catalog_coverage,
    load_catalog,
    load_release,
    validate_catalog,
)
from playmatch.config import Settings
from playmatch.main import create_app


def test_release_is_48_unique_games_with_preserved_historical_bytes():
    assert (
        hashlib.sha256((DATA / "catalog.json").read_bytes()).hexdigest()
        == "04b3470ffa38b4082c5d095993f477362a727e16bf0dd55649ed6ebc511e9f10"
    )
    catalog = load_catalog()
    assert len(catalog) == len({g["steam_app_id"] for g in catalog}) == 48
    assert catalog[:24] == json.loads((DATA / "catalog.json").read_text())
    assert catalog_coverage(catalog) == {
        "total_games": 48,
        "source_linked_games": 48,
        "description_observed": 24,
        "chinese_interface_verified": 32,
        "online_coop_verified": 16,
        "local_coop_verified": 1,
        "horror_unknown": 47,
        "with_aliases": 24,
        "automatic_sync": False,
        "notes": [
            "覆盖统计不等于全部属性核验，也不代表最低购买价。",
            "简介与别名包含开发助手整理内容，不是独立玩家评测。",
        ],
    }


def test_provenance_and_unknown_fields_are_not_guessed():
    audit = json.loads((DATA / "catalog-v06-provenance.json").read_text())
    extension = json.loads((DATA / "catalog-v06.json").read_text())
    assert not audit["independent_human_review"]
    assert len(audit["entries"]) == 24
    for game, entry in zip(extension, audit["entries"], strict=True):
        assert entry["game_id"] == game["id"]
        assert entry["source_url"] == game["store_url"]
        assert entry["reviewer_kind"] == "coding_agent"
        assert entry["facts"]["simplified_chinese_interface"] == game["chinese_support"]
        assert entry["facts"]["cooperative_mode"] == game["coop"]
    by_id = {g["steam_app_id"]: g for g in extension}
    assert by_id[289070]["coop"] == "unknown"  # Community co-op tag is not feature evidence.
    assert by_id[1677770]["chinese_support"] == "unknown"  # Traditional is not simplified.
    assert by_id[1055540]["chinese_support"] == "unknown"
    assert by_id[972660]["coop"] == "local"  # Remote Play Together is not native online co-op.


@pytest.mark.parametrize("mutation", ["checksum", "count", "total", "path", "repeat"])
def test_release_corruption_rejected(tmp_path, mutation):
    manifest = json.loads((DATA / "catalog-release.json").read_text())
    for part in manifest["parts"]:
        (tmp_path / part["path"]).write_bytes((DATA / part["path"]).read_bytes())
    if mutation == "checksum":
        manifest["parts"][0]["sha256"] = "0" * 64
    elif mutation == "count":
        manifest["parts"][0]["count"] += 1
    elif mutation == "total":
        manifest["total_games"] += 1
    elif mutation == "path":
        manifest["parts"][0]["path"] = "../catalog.json"
    else:
        manifest["parts"].append(manifest["parts"][0])
    (tmp_path / "catalog-release.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_release(tmp_path)


@pytest.mark.parametrize("mutation", ["duplicate", "steam_id", "url", "price", "evidence", "alias"])
def test_catalog_input_integrity(mutation):
    rows = deepcopy(load_catalog()[24:25])
    if mutation == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif mutation == "steam_id":
        rows[0]["steam_app_id"] = 0
    elif mutation == "url":
        rows[0]["store_url"] = "https://unrelated.invalid/"
    elif mutation == "price":
        rows[0]["price"] = 19.99
    elif mutation == "alias":
        rows[0]["aliases"] = ["x" * 81]
    else:
        rows[0]["evidence"] = []
    with pytest.raises(ValueError):
        validate_catalog(rows)


def test_custom_catalog_is_replacement_not_append(monkeypatch):
    monkeypatch.setenv("PLAYMATCH_CATALOG_PATH", str(DATA / "catalog.json"))
    assert len(load_catalog()) == 24


def test_update_adds_release_and_keeps_watchlist_across_restart(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///" + str(tmp_path / "upgrade.db"),
        run_execution_mode="external",
    )
    old = json.loads((DATA / "catalog.json").read_text())
    credentials = {"email": "upgrade@example.test", "password": "valid-test-password-472"}
    with TestClient(create_app(settings, catalog=old)) as client:
        assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
        assert (
            client.post(
                "/api/v1/watchlist",
                json={
                    "game_id": old[0]["id"],
                    "target_minor": 1000,
                    "currency": "USD",
                    "country": "US",
                },
            ).status_code
            == 200
        )
        assert client.get("/api/v1/catalog/coverage").json()["total_games"] == 24
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/auth/login", json=credentials).status_code == 200
        assert client.get("/api/v1/watchlist").json()["items"][0]["game"]["id"] == old[0]["id"]
        coverage = client.get("/api/v1/catalog/coverage")
        assert (
            coverage.json()["total_games"] == 48 and coverage.headers["cache-control"] == "no-store"
        )
        assert client.get("/api/v1/games?q=BG3").json()["items"][0]["steam_app_id"] == 1086940
        first = client.get("/api/v1/games?limit=24").json()
        second = client.get("/api/v1/games?offset=24&limit=24").json()
        assert first["total"] == second["total"] == 48
        assert len({g["id"] for g in first["items"] + second["items"]}) == 48


def test_coverage_counts_stored_data_not_configured_size(client):
    response = client.get("/api/v1/catalog/coverage")
    assert response.status_code == 200
    assert response.json()["total_games"] == 4
    assert (
        response.json()["chinese_interface_verified"] == 0
    )  # Synthetic fixture lacks field evidence.
