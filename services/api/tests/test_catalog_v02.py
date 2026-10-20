import copy
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, select, update

from playmatch.catalog_import import CatalogImportError, import_catalog
from playmatch.config import Settings
from playmatch.database import Database
from playmatch.main import create_app
from playmatch.models import CatalogImport, CatalogLock, CatalogSource, GameRecord
from playmatch.workflow import build_graph


@pytest.fixture
def database(tmp_path):
    url = os.getenv("CATALOG_TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "catalog.db"))
    db = Database(url)
    db.migrate()
    with db.sessions() as session, session.begin():
        for model in (CatalogImport, CatalogSource, GameRecord):
            session.execute(delete(model))
        session.execute(update(CatalogLock).values(revision=0))
    yield db
    db.close()


def item(provider="123", steam=100001):
    return {"provider_id": provider, "steam_app_id": steam, "title": "Synthetic Test",
            "description": "Synthetic metadata for tests only, not a real game.",
            "platforms": ["PC (Microsoft Windows)"], "themes": ["太空"], "genres": ["测试"],
            "image_url": "", "source_url": "https://www.igdb.com/games/synthetic-test"}


def manifest(*items, at="2026-10-19T00:00:00Z"):
    return {"schema_version": 1, "source": "igdb", "observed_at": at, "items": list(items or [item()])}


def counts(db):
    with db.sessions() as session:
        return tuple(len(list(session.scalars(select(model)))) for model in (GameRecord, CatalogSource, CatalogImport))


def test_default_dry_run_is_zero_write(database):
    report = import_catalog(database, manifest(), "dry")
    assert report["inserted"] == 1 and report["dry_run"]
    assert counts(database) == (0, 0, 0)
    with database.sessions() as session:
        assert session.get(CatalogLock, 1).revision == 0


def test_apply_replay_conflict(database):
    first = import_catalog(database, manifest(), "same", dry_run=False)
    assert first["inserted"] == 1
    assert import_catalog(database, manifest(), "same", dry_run=False)["replayed"]
    changed = manifest()
    changed["items"][0]["title"] = "changed"
    with pytest.raises(CatalogImportError):
        import_catalog(database, changed, "same", dry_run=False)
    assert counts(database) == (1, 1, 1)


def test_duplicates_and_order_have_canonical_key(database):
    a, b = item(), item("124", 100002)
    first = import_catalog(database, manifest(a, b, a), "dedupe", dry_run=False)
    second = import_catalog(database, manifest(b, a), "dedupe", dry_run=False)
    assert first["deduplicated"] == 1 and second["replayed"]
    assert counts(database) == (2, 2, 1)


@pytest.mark.parametrize("mutation", ["title", "steam_app_id", "provider_id"])
def test_conflicting_duplicates_reject_whole_batch(database, mutation):
    a, b = item(), item()
    b[mutation] = 100002 if mutation == "steam_app_id" else ("999" if mutation == "provider_id" else "changed")
    with pytest.raises(CatalogImportError):
        import_catalog(database, manifest(a, b), "conflict", dry_run=False)
    assert counts(database) == (0, 0, 0)


def test_late_conflict_rolls_back_earlier_insert(database):
    import_catalog(database, manifest(), "initial", dry_run=False)
    with pytest.raises(CatalogImportError):
        import_catalog(database, manifest(item("124", 100002), item("123", 100003)), "late", dry_run=False)
    assert counts(database) == (1, 1, 1)
    with database.sessions() as session:
        assert session.get(CatalogLock, 1).revision == 1


def test_curated_identity_and_metadata_are_not_overwritten(database, catalog):
    curated = copy.deepcopy(catalog[0])
    curated["steam_app_id"] = 100001
    with database.sessions() as session, session.begin():
        session.add(GameRecord(id=curated["id"], title=curated["title"], payload=curated))
    report = import_catalog(database, manifest(), "protect", dry_run=False)
    assert report["preserved"] == 1
    with database.sessions() as session:
        assert session.get(GameRecord, curated["id"]).payload == curated
        assert session.get(CatalogSource, "123").game_id == curated["id"]


def test_provider_hard_conditions_remain_unknown(database):
    import_catalog(database, manifest(), "unknown", dry_run=False)
    with database.sessions() as session:
        game = session.get(GameRecord, "steam-100001").payload
    assert game["chinese_support"] == game["coop"] == game["horror"] == "unknown"
    assert game["title_zh"] == game["title"] and game["estimated_hours"] is None
    output = build_graph(Settings(llm_api_key="", llm_model="", retrieval_mode="bm25"), lambda *_: None).invoke({
        "request": {"query": "中文太空", "country": "CN"}, "catalog": [game], "feedback": [],
    })
    assert not output["results"]


def test_update_stale_same_timestamp_and_restart(database):
    import_catalog(database, manifest(at="2026-10-19T00:00:00.500000Z"), "first", dry_run=False)
    changed = item()
    changed["description"] = "New reviewed description"
    for timestamp in ("2026-10-19T00:00:00Z", "2026-10-19T00:00:00.5Z"):
        with pytest.raises(CatalogImportError):
            import_catalog(database, manifest(changed, at=timestamp), timestamp.replace("+", ""), dry_run=False)
    report = import_catalog(database, manifest(changed, at="2026-10-19T01:00:00Z"), "newer", dry_run=False)
    assert report["updated"] == 1
    database.migrate()
    with database.sessions() as session:
        assert session.get(GameRecord, "steam-100001").payload["description"] == changed["description"]


def test_concurrent_idempotency_serializes(database):
    def run(_):
        return import_catalog(database, manifest(), "concurrent", dry_run=False)
    with ThreadPoolExecutor(max_workers=4) as executor:
        reports = list(executor.map(run, range(4)))
    assert sum(not report["replayed"] for report in reports) == 1
    assert counts(database) == (1, 1, 1)


@pytest.mark.parametrize("field,value", [
    ("title", " "), ("title", "x" * 301), ("description", ""),
    ("provider_id", "-1"), ("steam_app_id", True), ("steam_app_id", 0),
    ("source_url", "https://igdb.com.evil.invalid/games/test"),
    ("source_url", "https://user:pass@igdb.com/games/test"),
    ("source_url", "https://igdb.com/games/test?token=secret"),
    ("source_url", "http://igdb.com/games/test"),
    ("image_url", "https://evil.invalid/a.png"),
    ("themes", ["\n"]), ("platforms", ["x" * 81]),
    ("chinese_support", "verified"), ("price_minor", 0),
])
def test_invalid_records_are_never_committed(database, field, value):
    bad = item()
    bad[field] = value
    with pytest.raises((ValidationError, CatalogImportError)):
        import_catalog(database, manifest(bad), "invalid", dry_run=False)
    assert counts(database) == (0, 0, 0)


@pytest.mark.parametrize("timestamp", ["not-time", "2020-01-01T00:00:00", "2099-01-01T00:00:00Z"])
def test_invalid_observation_time(database, timestamp):
    with pytest.raises(ValidationError):
        import_catalog(database, manifest(at=timestamp), "invalid", dry_run=False)


def test_api_restart_and_browse_retains_import(database):
    import_catalog(database, manifest(), "persist", dry_run=False)
    config = Settings(database_url=str(database.engine.url), llm_api_key="", llm_model="")
    for _ in range(2):
        with TestClient(create_app(config, catalog=[])) as client:
            body = client.get("/api/v1/games?offset=0&limit=1").json()
            assert body["total"] == 1 and body["items"][0]["id"] == "steam-100001"
            assert client.get("/api/v1/games?offset=1&limit=1").json()["items"] == []
            assert client.get("/api/v1/capabilities").json()["catalog_mode"] == "mixed"
