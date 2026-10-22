"""Synthetic IGDB payloads only. Storage/API/worker behavior is real; no live credentials."""

import asyncio
import copy
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from playmatch.candidate_search import CatalogChanged, catalog_context, shortlist
from playmatch.catalog_search import coverage_sql
from playmatch.catalog_seed import catalog_coverage
from playmatch.config import Settings
from playmatch.database import Database
from playmatch.main import create_app
from playmatch.metadata_enrichment import (
    EnrichmentLeaseLost,
    MetadataEnricher,
    enrichment_status,
)
from playmatch.metadata_provider import enrich_record, lookup_batch
from playmatch.models import (
    CatalogSource,
    EnrichmentJob,
    EnrichmentState,
    GameRecord,
    GameSearchTerm,
    SearchRevision,
    SteamDirectoryApp,
)
from playmatch.providers.cheapshark import ProviderRequestError
from playmatch.providers.igdb import IGDBClient
from playmatch.workflow import build_graph, rule_intent
from sqlalchemy import delete, event, func, select, update


@pytest.fixture
def db(tmp_path):
    url = os.getenv("METADATA_TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "metadata.db"))
    if (
        os.getenv("METADATA_TEST_DATABASE_URL")
        and os.getenv("PLAYMATCH_ALLOW_DISPOSABLE_DB") != "yes"
    ):
        raise RuntimeError("Explicit disposable database required")
    database = Database(url)
    database.migrate()
    with database.sessions() as s, s.begin():
        for table in (
            EnrichmentJob,
            EnrichmentState,
            CatalogSource,
            GameSearchTerm,
            GameRecord,
            SteamDirectoryApp,
        ):
            s.execute(delete(table))
        s.add(EnrichmentState(id=1))
    yield database
    database.close()


def cfg(**extra):
    return Settings(
        _env_file=None,
        metadata_enrichment_enabled=True,
        igdb_client_id="synthetic-id",
        igdb_client_secret="synthetic-secret",
        llm_model="",
        llm_api_key="",
        **extra,
    )


def raw(appid=900001, provider=123):
    return {
        "id": provider,
        "name": "Synthetic Star Observatory",
        "summary": "Synthetic test only: explore a science fiction galaxy and solve puzzles.",
        "url": "https://www.igdb.com/games/synthetic-observatory",
        "updated_at": 1767225600,
        "game_type": {"type": "Main Game"},
        "platforms": [{"id": 6, "name": "PC (Microsoft Windows)"}],
        "themes": [{"name": "Science fiction"}],
        "genres": [{"name": "Puzzle"}],
        "external_games": [
            {
                "uid": str(appid),
                "url": f"https://store.steampowered.com/app/{appid}/",
                "external_game_source": {"name": "Steam"},
            }
        ],
        "language_supports": [
            {
                "language": {"name": "Chinese (Simplified)", "locale": "zh-CN"},
                "language_support_type": {"name": "Interface"},
            }
        ],
        "multiplayer_modes": [{"platform": 6, "onlinecoop": True}],
    }


def discover(db, appid=900001, name="Synthetic Star Observatory"):
    with db.sessions() as s, s.begin():
        s.add(
            SteamDirectoryApp(
                appid=appid,
                name=name,
                name_normalized=name.casefold(),
                source_modified=100,
                price_change_number=1,
                first_seen=100,
                last_seen=100,
                full_generation=1,
            )
        )


class FixtureClient:
    configured = True

    def __init__(self, rows=None):
        self.rows = [raw()] if rows is None else rows
        self.calls = []

    async def catalog_page(self, body):
        self.calls.append(body)
        return copy.deepcopy(self.rows)


def normalized_value(row=None, appid=900001):
    at = datetime.now(UTC).isoformat()
    value, error = enrich_record(raw() if row is None else row, appid, at)
    assert error is None
    return {**value, "observed_at": at}


def due(db, appid=900001):
    with db.sessions() as s, s.begin():
        s.execute(update(EnrichmentState).values(next_request_at=0, scan_due=0, scan_cursor=0))
        s.execute(update(EnrichmentJob).where(EnrichmentJob.appid == appid).values(available_at=0))


def test_end_to_end_discovery_enrichment_persistent_search_and_constraints(db):
    discover(db)
    fixture = FixtureClient()
    worker = MetadataEnricher(db, cfg(), fixture)
    assert asyncio.run(worker.tick())
    with db.sessions() as s:
        row = s.get(GameRecord, "steam-900001")
        assert row.metadata_origin == "igdb_auto"
        assert row.payload["title_zh"] == row.payload["title"]  # no invented translation
        assert row.payload["chinese_support"] == "verified"
        assert row.payload["coop"] == "online"
        assert row.payload["horror"] == "unknown"
        assert {"科幻", "解谜"} <= set(row.payload["themes"])
        assert s.scalar(select(func.count()).select_from(GameSearchTerm)) > 0
        assert s.get(EnrichmentJob, 900001).status == "ready"
        assert enrichment_status(s, cfg())["automatic_metadata"] == 1
    db.engine.dispose()
    with db.sessions() as s:
        rows, scope = shortlist(
            s, rule_intent({"query": "科幻解谜", "country": "CN"}), "科幻解谜", [], 32
        )
    output = build_graph(cfg(), lambda *_: None).invoke(
        {"request": {"query": "科幻解谜", "country": "CN"}, "catalog": rows, "feedback": []}
    )
    assert output["results"][0]["game"]["id"] == "steam-900001"
    assert scope["candidate_count"] == 1
    assert not asyncio.run(worker.tick())
    assert len(fixture.calls) == 1


def test_provider_request_is_exact_id_bounded_and_no_secrets_in_url():
    requests = []

    def handler(request):
        requests.append(request)
        assert "synthetic-secret" not in str(request.url)
        if request.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "fixture-token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer fixture-token"
        body = request.content.decode()
        assert 'external_games.uid = ("900001")' in body
        assert 'external_game_source.name = "Steam"' in body
        assert "search " not in body and "limit 51;" in body
        return httpx.Response(200, json=[raw()])

    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            client = IGDBClient(
                transport, client_id="synthetic-id", client_secret="synthetic-secret"
            )
            return await lookup_batch(client, [900001])

    results = asyncio.run(call())
    assert results[900001]["provider_id"] == "123"
    assert len(requests) == 2


@pytest.mark.parametrize("ids", [[], [True], [0], [-1], [4294967296], [1, 1], list(range(1, 12))])
def test_lookup_refuses_invalid_ids_without_network(ids):
    client = FixtureClient()
    with pytest.raises(ValueError):
        asyncio.run(lookup_batch(client, ids))
    assert not client.calls


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("dlc", "not_verified_base_game"),
        ("parent", "not_verified_base_game"),
        ("missing_summary", "invalid_metadata"),
        ("no_pc", "pc_platform_missing"),
        ("bad_timestamp", "invalid_source_timestamp"),
        ("future", "invalid_source_timestamp"),
    ],
)
def test_rejected_metadata(mutation, error):
    value = raw()
    if mutation == "dlc":
        value["game_type"] = {"type": "DLC / Addon"}
    if mutation == "parent":
        value["parent_game"] = 100
    if mutation == "missing_summary":
        value["summary"] = ""
    if mutation == "no_pc":
        value["platforms"] = [{"name": "PlayStation 5"}]
    if mutation == "bad_timestamp":
        value["updated_at"] = True
    if mutation == "future":
        value["updated_at"] = 9999999999
    _, actual = enrich_record(value, 900001, datetime.now(UTC).isoformat())
    assert actual == error


@pytest.mark.parametrize(
    "scenario", ["cross_platform", "missing_language", "traditional", "audio_only"]
)
def test_language_is_never_promoted_without_scoped_positive_evidence(scenario):
    data = raw()
    if scenario == "cross_platform":
        data["platforms"].append({"name": "PlayStation 5"})
    if scenario == "missing_language":
        del data["language_supports"]
    if scenario == "traditional":
        data["language_supports"][0]["language"] = {
            "locale": "zh-TW",
            "name": "Chinese (Traditional)",
        }
    if scenario == "audio_only":
        data["language_supports"][0]["language_support_type"]["name"] = "Audio"
    assert normalized_value(data)["payload"]["chinese_support"] == "unknown"


@pytest.mark.parametrize(
    "modes",
    [
        [],
        [{"platform": 48, "onlinecoop": True}],
        [{"platform": 6, "onlinecoop": "true"}],
        [{"platform": 6, "onlinecoop": False}],
    ],
)
def test_coop_is_platform_specific_and_missing_is_unknown(modes):
    data = raw()
    data["multiplayer_modes"] = modes
    assert normalized_value(data)["payload"]["coop"] == "unknown"


def test_unmatched_and_ambiguity_never_fabricate_games(db):
    discover(db)
    worker = MetadataEnricher(db, cfg(), FixtureClient([]))
    assert asyncio.run(worker.tick())
    with db.sessions() as s:
        assert s.get(GameRecord, "steam-900001") is None
        assert s.get(EnrichmentJob, 900001).status == "unmatched"
    due(db)
    worker.client = FixtureClient([raw(), raw(provider=124)])
    assert asyncio.run(worker.tick())
    with db.sessions() as s:
        assert s.get(EnrichmentJob, 900001).last_error == "ambiguous_mapping"
        assert s.get(GameRecord, "steam-900001") is None


def test_conflicting_uid_and_url_fails_closed():
    data = raw()
    data["external_games"][0]["uid"] = "900002"
    with pytest.raises(ProviderRequestError):
        asyncio.run(lookup_batch(FixtureClient([data]), [900001]))


def test_response_limit_is_not_silently_truncated():
    with pytest.raises(ProviderRequestError, match="ambiguous_response_limit"):
        asyncio.run(lookup_batch(FixtureClient([raw()] * 51), [900001]))


def test_source_change_fences_inflight_result(db):
    discover(db)
    worker = MetadataEnricher(db, cfg(), FixtureClient())
    claim = worker.claim()
    with db.sessions() as s, s.begin():
        s.get(SteamDirectoryApp, 900001).name = "Changed source name"
    worker.apply(claim, {900001: normalized_value()})
    with db.sessions() as s:
        assert s.get(GameRecord, "steam-900001") is None
        assert s.get(EnrichmentJob, 900001).status == "pending"
        assert s.get(EnrichmentJob, 900001).last_error == "discovery_changed"


def test_owner_expiry_recovery_rejects_late_write(db):
    discover(db)
    worker = MetadataEnricher(db, cfg(), FixtureClient())
    old = worker.claim()
    with db.sessions() as s, s.begin():
        s.get(EnrichmentState, 1).lease_until = 0
    due(db)
    replacement = worker.claim()
    assert replacement.token != old.token
    with pytest.raises(EnrichmentLeaseLost):
        worker.apply(old, {900001: normalized_value()})
    worker.apply(replacement, {900001: normalized_value()})
    with pytest.raises(EnrichmentLeaseLost):
        worker.apply(replacement, {900001: normalized_value()})


def test_concurrent_claims_have_one_owner(db):
    discover(db)
    worker = MetadataEnricher(db, cfg(), FixtureClient())
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(lambda _: worker.claim(), range(4)))
    assert sum(c is not None for c in claims) == 1


def test_disable_configuration_quota_and_sanitized_failures(db):
    discover(db)
    config = cfg(metadata_daily_requests=6)
    worker = MetadataEnricher(db, config, FixtureClient())
    config.metadata_enrichment_enabled = False
    assert worker.claim() is None
    config.metadata_enrichment_enabled = True
    claim = worker.claim()
    assert claim
    worker.fail(claim, "https://upstream.invalid/?token=SECRET")
    due(db)
    assert worker.claim() is None
    with db.sessions() as s:
        assert s.get(EnrichmentJob, 900001).last_error == "provider_unavailable"
        status = enrichment_status(s, config)
        assert status["last_error"] == "daily_budget_exhausted"
        assert status["reserved_requests_today"] == 6
        assert "SECRET" not in json.dumps(status)


def test_preserve_operator_metadata(db, catalog):
    discover(db)
    payload = {**catalog[0], "id": "steam-900001", "steam_app_id": 900001}
    with db.sessions() as s, s.begin():
        s.add(GameRecord(id=payload["id"], title=payload["title"], payload=payload))
    assert asyncio.run(MetadataEnricher(db, cfg(), FixtureClient()).tick())
    with db.sessions() as s:
        assert s.get(GameRecord, payload["id"]).payload == payload
        assert s.get(EnrichmentJob, 900001).status == "preserved"


def test_failure_during_postings_write_rolls_back_game_and_receipt(db):
    discover(db)
    worker = MetadataEnricher(db, cfg(), FixtureClient())
    claim = worker.claim()

    def explode(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO game_search_terms"):
            raise RuntimeError("synthetic write failure")

    event.listen(db.engine, "before_cursor_execute", explode)
    try:
        with pytest.raises(RuntimeError, match="synthetic write failure"):
            worker.apply(claim, {900001: normalized_value()})
    finally:
        event.remove(db.engine, "before_cursor_execute", explode)
    with db.sessions() as s:
        assert s.get(GameRecord, "steam-900001") is None
        assert s.scalar(select(func.count()).select_from(GameSearchTerm)) == 0
        assert s.get(EnrichmentJob, 900001).status == "processing"
        assert s.get(EnrichmentState, 1).last_success is None


def test_expired_automatic_metadata_is_not_recommended(db):
    discover(db)
    asyncio.run(MetadataEnricher(db, cfg(), FixtureClient()).tick())
    with db.sessions() as s, s.begin():
        s.get(GameRecord, "steam-900001").metadata_valid_until = 0
    with db.sessions() as s:
        rows, context = catalog_context(s, 256)
        assert not rows and context["eligible_count"] == 0
        assert enrichment_status(s, cfg())["automatic_metadata"] == 0


def test_rejected_refresh_blocks_old_automatic_metadata(db):
    discover(db)
    worker = MetadataEnricher(db, cfg(), FixtureClient())
    asyncio.run(worker.tick())
    due(db)
    worker.client = FixtureClient([raw(), raw(provider=124)])
    asyncio.run(worker.tick())
    with db.sessions() as s:
        assert s.get(GameRecord, "steam-900001").recommendation_blocked
        assert catalog_context(s, 256)[0] == []


def test_old_provider_version_cannot_overwrite_newer(db):
    discover(db)
    worker = MetadataEnricher(db, cfg(), FixtureClient())
    asyncio.run(worker.tick())
    due(db)
    changed = raw()
    changed["summary"] = "different at same timestamp"
    worker.client = FixtureClient([changed])
    asyncio.run(worker.tick())
    with db.sessions() as s:
        assert s.get(EnrichmentJob, 900001).last_error == "stale_provider_data"
        assert s.get(GameRecord, "steam-900001").payload["description"] == raw()["summary"]


def test_incremental_postings_update_and_delete(db, catalog):
    with db.sessions() as s, s.begin():
        s.add(GameRecord(id="test", title="A", payload=catalog[0]))
    with db.sessions() as s, s.begin():
        record = s.get(GameRecord, "test")
        record.payload = {**record.payload, "description": "quantumzebras"}
    with db.sessions() as s:
        assert s.scalar(select(GameSearchTerm).where(GameSearchTerm.term == "quantumzebras"))
        revision = s.get(SearchRevision, 1).revision
    with db.sessions() as s, s.begin():
        s.delete(s.get(GameRecord, "test"))
    with db.sessions() as s:
        assert s.scalar(select(func.count()).select_from(GameSearchTerm)) == 0
        assert s.get(SearchRevision, 1).revision > revision


def test_bounded_index_search_finds_long_tail_and_prefilters(db, catalog):
    with db.sessions() as s, s.begin():
        for i in range(80):
            p = {
                **catalog[0],
                "id": f"item-{i:03}",
                "steam_app_id": i + 10000,
                "description": "unrelated ordinary template",
                "title": "Ordinary",
            }
            if i == 79:
                p.update(description="rarequantumzebra science exploration", themes=["科幻"])
            s.add(GameRecord(id=p["id"], title=p["title"], payload=p))
    intent = rule_intent({"query": "rarequantumzebra", "country": "CN"})
    with db.sessions() as s:
        initial, context = catalog_context(s, 32)
        assert initial == [] and context["eligible_count"] == 80
        rows, scope = shortlist(s, intent, "rarequantumzebra", [], 32)
        assert [r["id"] for r in rows] == ["item-079"]
        assert scope["candidate_source"] == "sql_postings"
        with pytest.raises(CatalogChanged):
            shortlist(s, intent, "query", [], 32, -1)
        rows, _ = shortlist(
            s, intent, "rarequantumzebra", [{"game_id": "item-079", "kind": "owned"}], 32
        )
        assert not rows


def test_coverage_sql_matches_old_snapshot(db, catalog):
    with db.sessions() as s, s.begin():
        for game in catalog:
            s.add(GameRecord(id=game["id"], title=game["title"], payload=game))
    with db.sessions() as s:
        actual = coverage_sql(s)
        expected = catalog_coverage(catalog)
    assert {k: v for k, v in actual.items() if k != "notes"} == {
        k: v for k, v in expected.items() if k != "notes"
    }


def test_real_api_sees_enriched_game_and_recommendation(tmp_path):
    config = cfg(database_url="sqlite:///" + str(tmp_path / "api.db"))
    with TestClient(create_app(config, catalog=[])) as client:
        database = client.app.state.database
        discover(database)
        asyncio.run(MetadataEnricher(database, config, FixtureClient()).tick())
        status = client.get("/api/v1/catalog/enrichment/status")
        assert status.status_code == 200 and status.headers["cache-control"] == "no-store"
        assert status.json()["automatic_metadata"] == 1
        assert client.get("/api/v1/games?q=科幻").json()["total"] == 1
        result = client.post(
            "/api/v1/runs",
            json={"query": "科幻解谜", "country": "CN"},
            headers={"Idempotency-Key": "metadata-integration-0001"},
        )
        assert result.status_code == 202, result.text
        task = result.json()
        headers = {"X-Run-Token": task["access_token"]}
        for _ in range(100):
            run = client.get("/api/v1/runs/" + task["id"], headers=headers).json()
            if run["status"] in ("completed", "partial", "failed"):
                break
            time.sleep(0.05)
        assert run["status"] in ("completed", "partial"), run
        assert run["results"][0]["game"]["id"] == "steam-900001"
        assert run["intent"]["candidate_source"] == "complete_small_catalog"
        assert (
            client.get("/api/v1/catalog/steam/status").json()["with_recommendation_metadata"] == 1
        )


def test_large_sql_pipeline_enters_actual_recommendation_api(tmp_path, catalog):
    config = cfg(
        database_url="sqlite:///" + str(tmp_path / "large-api.db"), retrieval_candidate_limit=32
    )
    fixtures = [
        {
            **catalog[0],
            "id": f"filler-{i}",
            "steam_app_id": i + 100,
            "title": "Synthetic unrelated filler",
            "themes": ["田园"],
            "description": "unrelated filler",
        }
        for i in range(50)
    ]
    with TestClient(create_app(config, catalog=fixtures)) as client:
        database = client.app.state.database
        discover(database)
        asyncio.run(MetadataEnricher(database, config, FixtureClient()).tick())
        response = client.post(
            "/api/v1/runs",
            json={"query": "科幻解谜", "country": "CN"},
            headers={"Idempotency-Key": "large-index-integration-001"},
        )
        task = response.json()
        assert response.status_code == 202
        for _ in range(100):
            run = client.get(
                "/api/v1/runs/" + task["id"], headers={"X-Run-Token": task["access_token"]}
            ).json()
            if run["status"] in ("completed", "partial", "failed"):
                break
            time.sleep(0.05)
        assert run["status"] in ("completed", "partial"), run
        assert run["results"][0]["game"]["id"] == "steam-900001"
        assert run["intent"]["candidate_source"] == "sql_postings"
        assert run["intent"]["eligible_corpus_size"] == 51
        assert run["intent"]["candidate_count"] <= 32


def test_old_schema_rows_receive_index_backfill_without_payload_changes(tmp_path, catalog):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import MetaData, Table

    db = Database("sqlite:///" + str(tmp_path / "old.db"))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with db.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0006")
        connection.execute(
            Table("games", MetaData(), autoload_with=connection)
            .insert()
            .values(
                id="legacy",
                title="Legacy",
                payload=catalog[0],
                search_text="legacy",
                search_themes="",
            )
        )
    db.migrate()
    with db.sessions() as s:
        assert s.get(GameRecord, "legacy").payload == catalog[0]
        assert s.scalar(select(func.count()).select_from(GameSearchTerm)) > 0
    db.close()


def _claim_in_process(url, marker):
    from pathlib import Path

    database = Database(url)
    try:
        claim = MetadataEnricher(database, cfg(), FixtureClient()).claim()
        Path(marker).write_text(claim.token if claim else "none")
        if claim:
            time.sleep(60)  # Parent terminates this real process in the test.
    finally:
        database.close()


def test_killed_metadata_owner_can_be_replaced(db, tmp_path):
    import multiprocessing

    marker = tmp_path / "claim.marker"
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_claim_in_process,
        args=(db.engine.url.render_as_string(hide_password=False), str(marker)),
    )
    discover(db)
    process.start()
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists() and marker.read_text() != "none"
        process.kill()
        process.join(5)
        assert not process.is_alive()
        with db.sessions() as s, s.begin():
            s.get(
                EnrichmentState, 1
            ).lease_until = 0  # Explicit clock fixture, not 90s real waiting.
        due(db)
        assert asyncio.run(MetadataEnricher(db, cfg(), FixtureClient()).tick())
        with db.sessions() as s:
            assert s.get(EnrichmentJob, 900001).status == "ready"
            assert s.get(GameRecord, "steam-900001") is not None
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
