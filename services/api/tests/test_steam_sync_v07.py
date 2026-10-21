"""Synthetic Steam contract fixtures; no credentials or live catalog calls."""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from playmatch.config import Settings
from playmatch.database import Database
from playmatch.models import GameRecord, SteamDirectoryApp, SteamSyncReceipt, SteamSyncState
from playmatch.providers.steam_catalog import (
    SteamCatalogClient,
    SteamCatalogError,
    parse_page,
    retry_delay,
)
from playmatch.steam_sync import (
    SteamSynchronizer,
    SyncLeaseLost,
    directory_page,
    sync_status,
)
from sqlalchemy import delete, select, update


@pytest.fixture
def db(tmp_path):
    url = os.getenv("STEAM_TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "steam.db"))
    if os.getenv("STEAM_TEST_DATABASE_URL") and os.getenv("PLAYMATCH_ALLOW_DISPOSABLE_DB") != "yes":
        raise RuntimeError("Dedicated disposable database must be explicitly allowed")
    database = Database(url)
    database.migrate()
    with database.sessions() as session, session.begin():
        session.execute(delete(SteamDirectoryApp))
        session.execute(delete(SteamSyncReceipt))
        session.execute(delete(SteamSyncState))
        session.add(SteamSyncState(id=1))
    yield database
    database.close()


@pytest.fixture
def config():
    return Settings(
        _env_file=None,
        steam_sync_enabled=True,
        steam_web_api_key="synthetic-key-not-real",
        steam_sync_page_size=2,
        steam_sync_interval_seconds=300,
    )


def row(appid=10, name="Synthetic Game", modified=100):
    return {"appid": appid, "name": name, "last_modified": modified, "price_change_number": 7}


def page(*rows, cursor=0, more=False):
    return parse_page(
        {"response": {"apps": list(rows), "have_more_results": more}}, cursor, max(2, len(rows))
    )


def due(db, **changes):
    with db.sessions() as session, session.begin():
        session.execute(
            update(SteamSyncState).where(SteamSyncState.id == 1).values(next_due=0, **changes)
        )


def state(db):
    with db.sessions() as session:
        return session.get(SteamSyncState, 1)


def count(db):
    with db.sessions() as session:
        return len(list(session.scalars(select(SteamDirectoryApp.appid))))


@pytest.mark.parametrize(
    "payload,code",
    [
        ({}, "invalid_response"),
        ({"response": {}}, "invalid_response"),
        ({"response": {"apps": [row(), row()]}}, "non_advancing_cursor"),
        ({"response": {"apps": [row(0)]}}, "non_advancing_cursor"),
        ({"response": {"apps": [row(True)]}}, "invalid_response"),
        ({"response": {"apps": [row(2**32)]}}, "invalid_response"),
        ({"response": {"apps": [row(name=" ")]}}, "invalid_name"),
        ({"response": {"apps": [row(name="x\ny")]}}, "invalid_name"),
        ({"response": {"apps": [row(name="x" * 1025)]}}, "invalid_name"),
        ({"response": {"apps": [], "have_more_results": True}}, "non_advancing_cursor"),
        ({"response": {"apps": [row()], "have_more_results": "false"}}, "invalid_response"),
        ({"response": {"apps": [row()], "last_appid": 999}}, "cursor_mismatch"),
        ({"response": {"apps": [row(modified=-1)]}}, "invalid_response"),
    ],
)
def test_reject_bad_pages(payload, code):
    with pytest.raises(SteamCatalogError, match=code):
        parse_page(payload, 0, 2)


def test_short_page_is_not_implicit_eof_and_protobuf_empty():
    assert parse_page({"response": {"apps": [row()]}}, 0, 2).more
    assert not parse_page({"response": {"have_more_results": False}}, 10, 2).more
    assert (
        parse_page({"response": {"apps": [{"appid": 10, "name": "No timestamps"}]}}, 0, 2).apps[0][
            "last_modified"
        ]
        is None
    )


def test_auth_in_header_filters_and_no_secret_in_urls(caplog):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["x-webapi-key"] == "synthetic-secret"
        assert "synthetic-secret" not in str(request.url)
        params = json.loads(request.url.params["input_json"])
        assert params["include_games"] and not any(
            params[k]
            for k in ("include_dlc", "include_software", "include_videos", "include_hardware")
        )
        assert params["last_appid"] == 0 and params["if_modified_since"] == 99
        return httpx.Response(200, json={"response": {"apps": [row()], "have_more_results": False}})

    result = SteamCatalogClient("synthetic-secret", transport=httpx.MockTransport(handler)).page(
        0, 99, 2
    )
    assert result.cursor == 10 and len(requests) == 1
    assert "synthetic-secret" not in caplog.text


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
        (500, "upstream_unavailable"),
        (302, "upstream_unavailable"),
    ],
)
def test_http_errors_sanitized_not_retried(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="SECRET",
            headers={"Retry-After": "901", "Location": "https://evil.invalid"},
        )

    with pytest.raises(SteamCatalogError, match=code) as error:
        SteamCatalogClient("SECRET", transport=httpx.MockTransport(handler)).page(0, 0, 2)
    assert "SECRET" not in str(error.value) and len(calls) == 1
    if status == 429:
        assert error.value.retry_after == 901


def test_retry_dates_and_bounds():
    assert retry_delay("999999999") == 86400
    assert retry_delay("invalid") == 60
    assert retry_delay("Wed, 21 Oct 2015 07:28:00 GMT") == 1


def test_oversize_and_invalid_json():
    for response in (
        httpx.Response(200, content=b"x" * 8_000_001),
        httpx.Response(200, content=b"not json"),
    ):
        with pytest.raises(SteamCatalogError):
            SteamCatalogClient(
                "fixture", transport=httpx.MockTransport(lambda _, response=response: response)
            ).page(0, 0, 2)


def test_no_key_no_request_or_db_claim(db, config):
    missing = Settings(_env_file=None, steam_sync_enabled=True)
    assert SteamSynchronizer(db, missing).claim() is None
    with db.sessions() as session:
        assert sync_status(session, missing)["status"] == "not_configured"
    assert state(db).requests == 0


def test_full_resume_delta_watermark_and_receipt(db, config):
    worker = SteamSynchronizer(db, config)
    first = worker.claim()
    assert first.cursor == first.since == 0
    worker.apply(first, page(row(10), row(20), more=True))
    assert state(db).cursor == 20 and state(db).watermark == 0
    assert worker.claim() is None  # Shared pacing, not a per-process sleep.
    due(db)
    restarted = SteamSynchronizer(db, config)
    second = restarted.claim()
    assert second.cycle_id == first.cycle_id and second.cursor == 20
    restarted.apply(second, page(row(30), cursor=20))
    completed = state(db)
    assert completed.watermark > 0 and completed.cycle_id is None and completed.pages == 2
    with db.sessions() as session:
        receipt = session.get(SteamSyncReceipt, first.cycle_id)
        assert receipt.rows_seen == 3 and receipt.final_cursor == 30
    due(db)
    delta = restarted.claim()
    assert delta.since == completed.watermark - 300 and delta.cursor == 0
    restarted.apply(delta, page(row(10, "Renamed", 101), row(40)))
    assert count(db) == 4
    with db.sessions() as session:
        assert session.get(SteamDirectoryApp, 10).name == "Renamed"


def test_failure_keeps_page_and_failed_transaction_replay(db, config):
    worker = SteamSynchronizer(db, config)
    claim = worker.claim()
    from sqlalchemy import event

    def fail_commit(connection):
        raise RuntimeError("synthetic SQL commit failure")

    event.listen(db.engine, "commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="synthetic SQL"):
            worker.apply(claim, page(row()))
    finally:
        event.remove(db.engine, "commit", fail_commit)
    assert count(db) == 0 and state(db).cursor == 0 and state(db).watermark == 0
    worker.apply(claim, page(row()))
    assert count(db) == 1
    due(db)
    new = worker.claim()
    watermark = state(db).watermark
    worker.fail(new, SteamCatalogError("rate_limited", 901))
    assert state(db).cycle_id == new.cycle_id and state(db).watermark == watermark
    assert worker.claim() is None


def test_expired_worker_cannot_write_or_advance_watermark(db, config):
    worker = SteamSynchronizer(db, config)
    claim = worker.claim()
    due(db, lease_until=0)
    replacement = worker.claim()
    assert replacement.token != claim.token
    with pytest.raises(SyncLeaseLost):
        worker.apply(claim, page(row()))
    with pytest.raises(SyncLeaseLost):
        worker.fail(claim, SteamCatalogError("timeout"))
    worker.apply(replacement, page(row()))
    assert count(db) == 1


def test_concurrent_claim_only_one(db, config):
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: SteamSynchronizer(db, config).claim(), range(4)))
    assert len([c for c in claims if c]) == 1
    assert state(db).requests == 1


def test_daily_request_budget_survives_restart(db, config):
    config.steam_sync_daily_requests = 1
    worker = SteamSynchronizer(db, config)
    worker.apply(worker.claim(), page(row(10), row(20), more=True))
    due(db)
    assert SteamSynchronizer(db, config).claim() is None
    assert state(db).last_error == "daily_budget_exhausted"
    assert state(db).cursor == 20


def test_empty_full_rejected_but_empty_incremental_allowed(db, config):
    worker = SteamSynchronizer(db, config)
    claim = worker.claim()
    with pytest.raises(SteamCatalogError, match="empty_full_snapshot"):
        worker.apply(claim, page())
    assert state(db).watermark == 0
    worker.apply(claim, page(row()))
    due(db)
    claim = worker.claim()
    worker.apply(claim, page())
    assert state(db).cycle_id is None


def test_periodic_full_marks_absence_without_deletion(db, config):
    worker = SteamSynchronizer(db, config)
    worker.apply(worker.claim(), page(row(10), row(20)))
    due(db, last_full_success=1)
    claim = worker.claim()
    assert claim.since == 0 and state(db).generation == 2
    worker.apply(claim, page(row(20)))
    assert count(db) == 2
    with db.sessions() as session:
        status = sync_status(session, config)
    assert status["not_seen_in_last_full"] == 1


def test_directory_keyset_prefix_literal_wildcards_and_metadata_separation(db, config):
    config.steam_sync_page_size = 10
    worker = SteamSynchronizer(db, config)
    worker.apply(
        worker.claim(), page(row(10, "ＣＡＴ game"), row(20, "Cat%literal"), row(30, "Other"))
    )
    with db.sessions() as session:
        first = directory_page(session, q="cat", limit=1)
        assert first["items"][0]["appid"] == 10 and first["next_appid"] == 10
        second = directory_page(session, q="cat", limit=1, after_appid=10)
        assert second["items"][0]["appid"] == 20 and second["next_appid"] is None
        assert len(directory_page(session, q="cat%")["items"]) == 1
        assert directory_page(session, q="30")["items"][0]["appid"] == 30
        assert directory_page(session, q="9" * 100)["items"] == []
        assert all(
            item["metadata_status"] == "pending" for item in directory_page(session)["items"]
        )
        assert list(session.scalars(select(GameRecord.id))) == []
        status = sync_status(session, config)
        assert status["discovered_games"] == 3 and status["metadata_pending"] == 3
        assert not status["automatic_metadata_enrichment"]


def test_read_only_api_and_input_limits(client):
    response = client.get("/api/v1/catalog/steam/status")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "disabled" and body["discovered_games"] == 0
    assert client.get("/api/v1/catalog/steam/games").json()["items"] == []
    assert client.get("/api/v1/catalog/steam/games?limit=101").status_code == 422
    assert client.get("/api/v1/catalog/steam/games?after_appid=-1").status_code == 422
    assert client.post("/api/v1/catalog/steam/games", json={"price": 0}).status_code == 405


def test_status_does_not_equate_configuration_or_heartbeat_with_success(db, config):
    with db.sessions() as session:
        assert sync_status(session, config)["status"] == "worker_not_running"
    worker = SteamSynchronizer(db, config)
    claim = worker.claim()
    with db.sessions() as session:
        assert sync_status(session, config)["status"] == "syncing"
    worker.fail(claim, SteamCatalogError("authentication_failed", 3600))
    with db.sessions() as session:
        result = sync_status(session, config)
        assert result["status"] == "backoff" and result["last_success_at"] is None
    due(db, heartbeat_at=0)
    with db.sessions() as session:
        assert sync_status(session, config)["status"] == "worker_not_running"


def test_older_upstream_payload_does_not_regress_metadata(db, config):
    worker = SteamSynchronizer(db, config)
    worker.apply(worker.claim(), page(row(10, "Newer", 500)))
    due(db)
    worker.apply(worker.claim(), page(row(10, "Stale cached name", 100)))
    with db.sessions() as session:
        assert session.get(SteamDirectoryApp, 10).name == "Newer"
        assert session.get(SteamDirectoryApp, 10).source_modified == 500


def test_metadata_sql_filter_and_wildcard_literal(client):
    result = client.get("/api/v1/games?q=FARM&theme=经营&limit=1").json()
    assert result["total"] == 1 and result["items"][0]["id"] == "farm"
    assert client.get("/api/v1/games?q=%25").json()["total"] == 0
    assert client.get("/api/v1/games?theme=经").json()["total"] == 0
    assert client.get("/api/v1/games?offset=3&limit=1").json()["total"] == 4


def test_schema_upgrade_preserves_games_and_backfills_search(tmp_path):
    from pathlib import Path

    import playmatch.database
    from alembic import command
    from alembic.config import Config
    from conftest import game

    db = Database("sqlite:///" + str(tmp_path / "upgrade.db"))
    config = Config(str(Path(playmatch.database.__file__).parents[1] / "alembic.ini"))
    with db.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0005")
        from sqlalchemy import text

        connection.execute(
            text("INSERT INTO games (id,title,payload) VALUES (:id,:title,:payload)"),
            {
                "id": "legacy",
                "title": "Legacy",
                "payload": json.dumps(game("legacy", "Legacy", ["经营"])),
            },
        )
    db.migrate()
    with db.sessions() as session:
        from playmatch.catalog_search import metadata_page

        assert metadata_page(session, q="legacy", theme="经营")["total"] == 1
        assert session.get(GameRecord, "legacy").payload["id"] == "legacy"
    db.close()


def test_long_unicode_names_fit_search_index_without_losing_source(db, config):
    worker = SteamSynchronizer(db, config)
    name = "中" * 1000
    worker.apply(worker.claim(), page(row(10, name)))
    with db.sessions() as session:
        stored = session.get(SteamDirectoryApp, 10)
        assert stored.name == name and len(stored.name_normalized) == 512
        assert directory_page(session, q="中" * 100)["items"][0]["appid"] == 10


def test_killed_page_reader_restarts_without_losing_cursor(db, config, tmp_path):
    import subprocess
    import sys
    import time

    marker = tmp_path / "page-request-started"
    environment = {
        **os.environ,
        "DATABASE_URL": db.engine.url.render_as_string(hide_password=False),
        "STEAM_SYNC_ENABLED": "true",
        "STEAM_WEB_API_KEY": "SYNTHETIC",
        "MARKER": str(marker),
    }
    blocked = """
import os, time
from pathlib import Path
from unittest.mock import patch
from playmatch.steam_worker import main
class Blocked:
    def page(self, *args):
        Path(os.environ['MARKER']).write_text('synthetic provider awaiting response')
        time.sleep(60)
with patch('playmatch.steam_sync.SteamCatalogClient', return_value=Blocked()):
    main()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", blocked],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
    finally:
        process.kill()
        process.communicate(timeout=5)
    assert state(db).cursor == 0 and state(db).watermark == 0 and count(db) == 0
    due(db, lease_until=0)  # Explicit simulated expiry; process was really killed.
    replacement = """
import sys
from unittest.mock import patch
from playmatch.providers.steam_catalog import parse_page
from playmatch.steam_worker import main
class Fixture:
    def page(self, cursor, since, limit):
        return parse_page({'response': {'apps': [{'appid': 10, 'name': 'Synthetic recovery'}],
                                       'have_more_results': False}}, cursor, limit)
sys.argv = ['steam-worker', '--once']
with patch('playmatch.steam_sync.SteamCatalogClient', return_value=Fixture()):
    raise SystemExit(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", replacement],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0 and "cycle_completed" in result.stdout
    assert count(db) == 1 and state(db).watermark > 0
