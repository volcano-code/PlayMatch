"""Contract fixtures only; no live IGDB credentials or production catalog claims."""

import copy
import gzip
import json

import httpx
import pytest
from playmatch.catalog_collect import collect_catalog, mapped_steam_id, unwrap_review, write_review
from playmatch.catalog_import import import_catalog
from playmatch.database import Database
from playmatch.providers.cheapshark import MAX_RESPONSE_BYTES, ProviderRequestError
from playmatch.providers.igdb import IGDBClient


def raw(id_=1, steam=100001):
    return {
        "id": id_,
        "name": "Synthetic fixture game",
        "summary": "Synthetic metadata, not a real game.",
        "url": "https://www.igdb.com/games/fixture-game",
        "game_type": {"type": "Main Game"},
        "platforms": [{"name": "PC (Microsoft Windows)"}],
        "themes": [{"name": "Science fiction"}],
        "external_games": [
            {
                "uid": str(steam),
                "external_game_source": {"name": "Steam"},
                "url": f"https://store.steampowered.com/app/{steam}/",
            }
        ],
    }


async def no_wait(_):
    pass


def handler_for(pages):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "private-token", "expires_in": 3600})
        body = request.content.decode()
        assert "sort id asc" in body and "platforms = (6)" in body
        assert "external_games.external_game_source.name" in body and "game_type.type" in body
        value = pages.pop(0)
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)

    return handler, calls


async def collect(responses, **kwargs):
    handler, calls = handler_for(responses)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = IGDBClient(
            http, client_id="fixture", client_secret="private-secret", sleep=no_wait
        )
        bundle = await collect_catalog(client, **kwargs)
    return bundle, calls


async def test_keyset_collection_and_exact_review_to_import(tmp_path):
    bundle, calls = await collect(
        [[raw(1), raw(2, 100002)], [raw(8, 100008)]], pages=2, page_size=2
    )
    assert bundle["scanned"] == bundle["accepted"] == 3
    assert bundle["next_after_id"] == 8 and bundle["source_exhausted"]
    assert "where id > 2" in calls[-1].content.decode()
    assert len([request for request in calls if request.url.host == "id.twitch.tv"]) == 1
    assert "private-token" not in json.dumps(bundle) and "private-secret" not in json.dumps(bundle)
    path = tmp_path / "review.json"
    write_review(path, bundle)
    parsed = json.loads(path.read_text())
    with pytest.raises(ValueError):
        unwrap_review(parsed, apply=True)
    manifest, review_hash = unwrap_review(parsed, approval=bundle["review_sha256"], apply=True)
    db = Database("sqlite:///" + str(tmp_path / "review.db"))
    try:
        db.migrate()
        preview = import_catalog(db, manifest, "review-1", review_hash=review_hash)
        assert preview["dry_run"] and preview["inserted"] == 3
        applied = import_catalog(db, manifest, "review-1", dry_run=False, review_hash=review_hash)
        assert applied["review_sha256"] == bundle["review_sha256"]
        assert import_catalog(db, manifest, "review-1", dry_run=False)["replayed"]
    finally:
        db.close()


async def test_page_budget_is_not_claimed_as_complete():
    result, _ = await collect([[raw(1)]], page_size=1)
    assert result["pages_fetched"] == 1 and not result["source_exhausted"]


@pytest.mark.parametrize(
    "rows",
    [[raw(2), raw(1)], [raw(1), raw(1)], [{**raw(), "id": True}], ["bad"], [{**raw(), "id": 0}]],
)
async def test_invalid_page_and_cursor_stop_without_review(rows):
    with pytest.raises(ProviderRequestError):
        await collect([rows], page_size=2)


async def test_upstream_failure_does_not_publish_partial_batch():
    with pytest.raises(ProviderRequestError):
        await collect([[raw(1)], httpx.Response(500, text="private-secret")], pages=2, page_size=1)


async def test_current_source_not_configured_never_networks():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected request"))
    ) as http:
        with pytest.raises(ProviderRequestError, match="not_configured"):
            await collect_catalog(IGDBClient(http, client_id="", client_secret=""))


@pytest.mark.parametrize(
    "mutation",
    [
        {"game_type": {"type": "DLC Addon"}},
        {"game_type": {"type": "Bundle"}},
        {"game_type": None},
        {"version_parent": 42},
        {"parent_game": 42},
        {"platforms": [{"name": "PlayStation 5"}]},
        {"summary": ""},
        {"external_games": []},
    ],
)
async def test_non_base_or_unverifiable_records_are_reported_not_imported(mutation):
    result, _ = await collect([[{**raw(), **mutation}]])
    assert result["accepted"] == 0 and len(result["rejected"]) == 1
    assert not result["manifest"]["items"]
    with pytest.raises(ValueError):
        unwrap_review(result, approval=result["review_sha256"], apply=True)


async def test_same_steam_mapping_for_multiple_works_rejects_both():
    result, _ = await collect([[raw(1), raw(2)]])
    assert result["accepted"] == 0
    assert all(row["reason"] == "cross_record_mapping_conflict" for row in result["rejected"])


@pytest.mark.parametrize(
    "url",
    [
        "https://store.steampowered.com/sub/100001/",
        "https://store.steampowered.com/app/999/",
        "https://user:pass@store.steampowered.com/app/100001/",
        "http://store.steampowered.com/app/100001/",
        "https://store.steampowered.com.evil.invalid/app/100001/",
        "https://store.steampowered.com/app/100001/?token=x",
    ],
)
def test_steam_mapping_conflicts_and_unsafe_urls(url):
    row = raw()
    row["external_games"][0]["url"] = url
    assert mapped_steam_id(row) is None


def test_multi_steam_edition_mapping_is_rejected():
    row = raw()
    row["external_games"].extend(raw(2, 200002)["external_games"])
    assert mapped_steam_id(row) is None


async def test_review_tampering_and_no_clobber(tmp_path):
    bundle, _ = await collect([[raw()]])
    modified = copy.deepcopy(bundle)
    modified["manifest"]["items"][0]["steam_app_id"] = 999
    with pytest.raises(ValueError):
        unwrap_review(modified, approval=bundle["review_sha256"], apply=True)
    path = tmp_path / "existing.json"
    path.write_text("original")
    with pytest.raises(FileExistsError):
        write_review(path, bundle)
    assert path.read_text() == "original" and not list(tmp_path.glob(".review-*"))


async def test_response_stream_limit_and_gzip():
    def handler(request):
        if request.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "private-token", "expires_in": 3600})
        return httpx.Response(
            200,
            content=gzip.compress(json.dumps([raw()]).encode()),
            headers={"Content-Encoding": "gzip"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = IGDBClient(http, client_id="fixture", client_secret="secret", sleep=no_wait)
        result = await collect_catalog(client)
        assert result["accepted"] == 1
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        )
    ) as http:
        with pytest.raises(ProviderRequestError):
            await collect_catalog(IGDBClient(http, client_id="id", client_secret="secret"))


@pytest.mark.parametrize(
    "kwargs",
    [{"after_id": True}, {"after_id": -1}, {"pages": 5}, {"page_size": 51}, {"pages": False}],
)
async def test_invalid_bounds_do_not_call_network(kwargs):
    async with IGDBClient(client_id="", client_secret="") as client:
        with pytest.raises(ValueError):
            await collect_catalog(client, **kwargs)
