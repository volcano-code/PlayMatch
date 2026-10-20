from urllib.parse import parse_qs

import httpx
from playmatch.providers.igdb import IGDBClient


async def test_unconfigured_is_explicit_and_never_networks():
    def forbidden(_):
        raise AssertionError("unconfigured provider must not perform requests")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        client = IGDBClient(http, client_id="", client_secret="")
        assert client.health()["status"] == "not_configured"
        result = await client.search_games("Stardew Valley")
    assert result["status"] == "not_configured"
    assert result["items"] == []


async def test_token_is_form_body_cached_and_search_escaped():
    calls = []
    sleeps = []

    async def no_wait(delay):
        sleeps.append(delay)

    def handler(request):
        calls.append(request)
        assert "secret-test" not in str(request.url)
        if request.url.host == "id.twitch.tv":
            values = parse_qs(request.content.decode())
            assert values["client_secret"] == ["secret-test"]
            assert values["grant_type"] == ["client_credentials"]
            return httpx.Response(200, json={"access_token": "token-test", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer token-test"
        assert request.headers["Client-ID"] == "client-test"
        assert 'search "a\\"; limit 500;"; limit 2;' in request.content.decode()
        return httpx.Response(
            200,
            json=[
                {
                    "id": 17000,
                    "name": "Stardew Valley",
                    "summary": "A farm game.",
                    "url": "https://www.igdb.com/games/stardew-valley",
                    "themes": [{"name": "Sandbox"}],
                    "genres": [{"name": "Simulator"}],
                    "platforms": [{"name": "PC (Microsoft Windows)"}],
                    "external_games": [{"url": "https://store.steampowered.com/app/413150/"}],
                    "cover": {"url": "//images.igdb.com/igdb/image/upload/t_thumb/abc.jpg"},
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = IGDBClient(
            http, client_id="client-test", client_secret="secret-test", sleep=no_wait
        )
        first = await client.search_games('a"; limit 500;', limit=2)
        second = await client.search_games('a"; limit 500;', limit=2)
    assert len([r for r in calls if r.url.host == "id.twitch.tv"]) == 1
    assert first["status"] == second["status"] == "ok"
    assert first["items"][0]["steam_app_id"] == 413150
    assert first["items"][0]["themes"] == ["Sandbox"]
    assert first["items"][0]["image_url"].startswith("https://images.igdb.com/")
    assert first["items"][0]["source_url"] == "https://www.igdb.com/games/stardew-valley"
    assert "secret-test" not in str(first)
    assert "token-test" not in str(first)
    assert sleeps and 0 < sleeps[0] <= 0.26


async def test_invalid_credentials_do_not_leak_error_body():
    def handler(_):
        return httpx.Response(401, json={"message": "secret-test-token must not leak"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await IGDBClient(http, client_id="id", client_secret="secret-test").search_games(
            "farm"
        )
    assert result["status"] == "unavailable"
    assert "secret-test" not in str(result)


async def test_controls_in_search_are_rejected_before_network():
    def forbidden(_):
        raise AssertionError("invalid query must not perform requests")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        client = IGDBClient(http, client_id="id", client_secret="secret")
        assert (await client.search_games("farm\nlimit 50"))["status"] == "unavailable"


async def test_missing_or_invalid_metadata_is_not_made_up():
    def handler(request):
        if request.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(
            200, json=[{"id": 42, "name": "Example"}, {"id": True, "name": "bad"}]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await IGDBClient(http, client_id="id", client_secret="secret").search_games(
            "Example"
        )
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["steam_app_id"] is None
    assert item["themes"] == []
    assert "chinese_support" not in item
    assert item["source_url"] == "https://api.igdb.com/v4/games"
