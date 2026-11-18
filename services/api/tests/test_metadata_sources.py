import asyncio
import json

import httpx
import pytest
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
        steam_payload(), 570, observed, rawg=rawg_payload()
    )
    assert error is None
    assert "MOBA" in value["payload"]["genres"]
    assert "Strategy" in value["payload"]["themes"]
    assert value["payload"]["horror"] == "yes"
    assert {row["provider_id"] for row in value["sources"]} == {"steam-store:570", "rawg:42"}
    assert len([row for row in value["payload"]["evidence"] if "RAWG" in row["label"]]) == 2


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
            return httpx.Response(200, json={"results": [rawg_payload(999)]})
        raise AssertionError("detail endpoint must not be requested without exact link")

    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rawg = RAWGClient("fixture-secret", client, spacing_seconds=0)
            return await rawg.lookup_by_steam_id(570, "Dota 2")

    assert asyncio.run(call()) is None
    assert len(requests) == 1
    assert "fixture-secret" in requests[0].url.params["key"]


def test_rawg_detail_is_checked_again_after_search():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/games":
            return httpx.Response(200, json={"results": [rawg_payload(570)]})
        return httpx.Response(200, json=rawg_payload(999))

    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rawg = RAWGClient("fixture-secret", client, spacing_seconds=0)
            return await rawg.lookup_by_steam_id(570, "Dota 2")

    assert asyncio.run(call()) is None
    assert len(calls) == 2


def test_steam_rawg_provider_degrades_to_steam_if_rawg_unavailable():
    class SteamFixture:
        async def lookup_batch(self, appids):
            return {570: {"data": steam_payload(570)}}

    class RawgFixture:
        configured = True

        async def lookup_by_steam_id(self, appid, title):
            raise ProviderRequestError("rate_limited")

    provider = SteamRawgMetadataProvider(
        steam_client=SteamFixture(), rawg_client=RawgFixture()
    )
    result = asyncio.run(provider.lookup_batch([570]))[570]
    assert result["payload"]["title"] == "Dota 2"
    assert {row["provider_id"] for row in result["sources"]} == {"steam-store:570"}
