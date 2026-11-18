import asyncio
import os

import httpx
from playmatch.config import Settings
from playmatch.database import Database
from playmatch.metadata_enrichment import MetadataEnricher
from playmatch.models import CatalogSource, EnrichmentJob, GameRecord, SteamDirectoryApp
from playmatch.providers.cheapshark import ProviderRequestError
from playmatch.providers.metadata_sources import (
    RAWGClient,
    SteamRawgMetadataProvider,
    SteamStoreClient,
    _rawg_store_ids,
    normalize_steam_app,
    plain_text,
)


def steam_payload(appid=570):
    return {
        "type": "game",
        "name": "Dota 2",
        "steam_appid": appid,
        "about_the_game": "<h2>About</h2><p>Play <b>together</b> in a fantasy arena.</p>",
        "short_description": "Short summary",
        "header_image": "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/570/header.jpg",
        "platforms": {"windows": True, "mac": True, "linux": True},
        "supported_languages": "English, French, <strong>Simplified Chinese</strong>",
        "genres": [{"id": "1", "description": "Action"}],
        "categories": [
            {"id": 1, "description": "Multi-player"},
            {"id": 36, "description": "Online Co-op"},
        ],
    }


def rawg_payload(appid=570):
    return {
        "id": 42,
        "slug": "dota-2",
        "name": "Dota 2",
        "description_raw": "RAWG supplemental description",
        "stores": [
            {
                "store": {"id": 1, "name": "Steam"},
                "url": f"https://store.steampowered.com/app/{appid}/",
            }
        ],
        "genres": [{"name": "MOBA"}],
        "tags": [{"name": "Horror"}, {"name": "Strategy"}],
    }


def test_plain_text_removes_markup_and_bounds_description():
    assert plain_text("<p>Hello <b>world</b></p>") == "Hello world"
    assert len(plain_text("<p>" + "x" * 7000 + "</p>")) == 6000


def test_steam_normalizer_keeps_unknowns_and_positive_evidence_only():
    value, error = normalize_steam_app(steam_payload(), 570, "2026-11-18T00:00:00+00:00")
    assert error is None
    game = value["payload"]
    assert game["id"] == "steam-570"
    assert game["description"] == "About Play together in a fantasy arena."
    assert game["chinese_support"] == "verified"
    assert game["coop"] == "online"
    assert game["horror"] == "unknown"
    assert game["platforms"] == ["PC"]
    assert len(value["sources"]) == 1
    assert value["sources"][0]["provider_id"] == "steam-store:570"


def test_steam_normalizer_rejects_non_games_non_pc_and_identity_mismatch():
    observed = "2026-11-18T00:00:00+00:00"
    assert normalize_steam_app(steam_payload(571), 570, observed)[1] == "identity_conflict"
    dlc = steam_payload()
    dlc["type"] = "dlc"
    assert normalize_steam_app(dlc, 570, observed)[1] == "not_verified_base_game"
    mac_only = steam_payload()
    mac_only["platforms"]["windows"] = False
    assert normalize_steam_app(mac_only, 570, observed)[1] == "pc_platform_missing"


def test_rawg_is_only_attached_when_exact_steam_store_url_matches():
    observed = "2026-11-18T00:00:00+00:00"
    value, error = normalize_steam_app(
        steam_payload(),
        570,
        observed,
        rawg={**rawg_payload(), "_playmatch_verified_steam_appid": 570},
    )
    assert error is None
    assert "MOBA" in value["payload"]["genres"]
    assert "Strategy" in value["payload"]["themes"]
    assert value["payload"]["horror"] == "yes"
    assert {row["provider_id"] for row in value["sources"]} == {"steam-store:570", "rawg:42"}
    assert len([row for row in value["payload"]["evidence"] if "RAWG" in row["label"]]) == 3


def test_rawg_store_mapping_rejects_lookalike_and_untrusted_urls():
    assert _rawg_store_ids(rawg_payload()) == {570}
    for url in (
        "http://store.steampowered.com/app/570/",
        "https://store.steampowered.com.evil.invalid/app/570/",
        "https://store.steampowered.com/app/570/?redirect=1",
    ):
        row = rawg_payload()
        row["stores"][0]["url"] = url
        assert _rawg_store_ids(row) == set()


def test_steam_store_client_batches_appids_and_validates_identity():
    seen = []

    async def call():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (
                    seen.append(request)
                    or httpx.Response(
                        200,
                        json={
                            "570": {"success": True, "data": steam_payload(570)},
                            "730": {"success": False},
                        },
                    )
                )
            )
        ) as client:
            return await SteamStoreClient(client).lookup_batch([570, 730])

    result = asyncio.run(call())
    assert result[570]["data"]["steam_appid"] == 570
    assert result[730]["error"] == "unmatched"
    assert len(seen) == 1
    assert seen[0].url.host == "store.steampowered.com"
    assert seen[0].url.params["appids"] == "570,730"


def test_rawg_client_requires_exact_steam_store_link():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/games":
            return httpx.Response(200, json={"results": [{"id": 42, "name": "Dota 2"}]})
        if request.url.path == "/api/games/42/stores":
            return httpx.Response(
                200,
                json={"results": [{"url": "https://store.steampowered.com/app/999/"}]},
            )
        raise AssertionError("detail endpoint must not be requested without exact link")

    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rawg = RAWGClient("fixture-secret", client, spacing_seconds=0)
            return await rawg.lookup_by_steam_id(570, "Dota 2")

    assert asyncio.run(call()) is None
    assert len(requests) == 2
    assert all("fixture-secret" in request.url.params["key"] for request in requests)


def test_rawg_client_accepts_only_exact_store_link_from_documented_endpoint():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/games":
            return httpx.Response(200, json={"results": [{"id": 42, "name": "Dota 2"}]})
        if request.url.path == "/api/games/42/stores":
            return httpx.Response(
                200,
                json={"results": [{"url": "https://store.steampowered.com/app/570/"}]},
            )
        if request.url.path == "/api/games/42":
            return httpx.Response(200, json={**rawg_payload(570), "id": 42})
        raise AssertionError(request.url.path)

    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rawg = RAWGClient("fixture-secret", client, spacing_seconds=0)
            return await rawg.lookup_by_steam_id(570, "Dota 2")

    result = asyncio.run(call())
    assert result["id"] == 42
    assert result["_playmatch_verified_steam_appid"] == 570
    assert calls == ["/api/games", "/api/games/42/stores", "/api/games/42"]


def test_rawg_detail_is_checked_again_after_search():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/games":
            return httpx.Response(200, json={"results": [{"id": 42, "name": "Dota 2"}]})
        if request.url.path == "/api/games/42/stores":
            return httpx.Response(
                200,
                json={"results": [{"url": "https://store.steampowered.com/app/570/"}]},
            )
        return httpx.Response(200, json={**rawg_payload(570), "id": 999})

    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rawg = RAWGClient("fixture-secret", client, spacing_seconds=0)
            return await rawg.lookup_by_steam_id(570, "Dota 2")

    assert asyncio.run(call()) is None
    assert len(calls) == 3


def test_steam_rawg_provider_degrades_to_steam_if_rawg_unavailable():
    class SteamFixture:
        async def lookup_batch(self, appids):
            return {570: {"data": steam_payload(570)}}

    class RawgFixture:
        configured = True

        async def lookup_by_steam_id(self, appid, title):
            raise ProviderRequestError("rate_limited")

    provider = SteamRawgMetadataProvider(steam_client=SteamFixture(), rawg_client=RawgFixture())
    result = asyncio.run(provider.lookup_batch([570]))[570]
    assert result["payload"]["title"] == "Dota 2"
    assert {row["provider_id"] for row in result["sources"]} == {"steam-store:570"}


def test_migration_allows_multiple_provenance_sources_for_one_game(tmp_path):
    url = os.getenv("METADATA_SOURCES_TEST_DATABASE_URL")
    if url and os.getenv("PLAYMATCH_ALLOW_DISPOSABLE_DB") != "yes":
        raise RuntimeError("Explicit disposable database permission required")
    db = Database(url or ("sqlite:///" + str(tmp_path / "multi-source.db")))
    try:
        db.migrate()
        with db.sessions() as session, session.begin():
            session.add(
                GameRecord(
                    id="steam-570",
                    title="Dota 2",
                    payload={"id": "steam-570", "title": "Dota 2"},
                )
            )
            session.flush()
            session.add_all(
                [
                    CatalogSource(
                        provider_id="steam-store:570",
                        game_id="steam-570",
                        observed_at="2026-11-18T00:00:00+00:00",
                        content_hash="a" * 64,
                    ),
                    CatalogSource(
                        provider_id="rawg:42",
                        game_id="steam-570",
                        observed_at="2026-11-18T00:00:00+00:00",
                        content_hash="b" * 64,
                    ),
                ]
            )
        with db.sessions() as session:
            rows = session.query(CatalogSource).filter_by(game_id="steam-570").all()
            assert {row.provider_id for row in rows} == {"steam-store:570", "rawg:42"}
    finally:
        db.close()


def test_metadata_worker_persists_steam_and_rawg_provenance_atomically(tmp_path):
    db = Database("sqlite:///" + str(tmp_path / "metadata-worker.db"))
    try:
        db.migrate()
        with db.sessions() as session, session.begin():
            session.add(
                SteamDirectoryApp(
                    appid=570,
                    name="Dota 2",
                    name_normalized="dota 2",
                    source_modified=100,
                    price_change_number=1,
                    first_seen=1,
                    last_seen=1,
                    full_generation=1,
                )
            )

        class ProviderFixture:
            request_reservation = 51

            async def lookup_batch(self, appids):
                assert appids == [570]
                value, error = normalize_steam_app(
                    steam_payload(),
                    570,
                    "2026-11-18T00:00:00+00:00",
                    rawg={**rawg_payload(), "_playmatch_verified_steam_appid": 570},
                )
                assert error is None
                return {570: value}

        settings = Settings(
            _env_file=None,
            metadata_enrichment_enabled=True,
            metadata_provider="steam_rawg",
        )
        worker = MetadataEnricher(db, settings, ProviderFixture())
        assert asyncio.run(worker.tick())
        with db.sessions() as session:
            game = session.get(GameRecord, "steam-570")
            job = session.get(EnrichmentJob, 570)
            sources = session.query(CatalogSource).filter_by(game_id="steam-570").all()
            assert game.metadata_origin == "provider_auto"
            assert job.status == "ready"
            assert {source.provider_id for source in sources} == {
                "steam-store:570",
                "rawg:42",
            }
            assert "MOBA" in game.payload["genres"]
    finally:
        db.close()
