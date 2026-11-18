"""Steam-first, multi-source metadata providers.

Steam Store AppDetails is the primary source. RAWG is an optional enrichment source
and is accepted only when its returned store link resolves to the exact Steam AppID.
No title-only match is ever used to attach RAWG metadata to a Steam identity.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlparse

import httpx

from playmatch.catalog_import import digest
from playmatch.providers.cheapshark import ProviderRequestError
from playmatch.schemas import Game

STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
RAWG_BASE_URL = "https://api.rawg.io/api"
MAX_RESPONSE_BYTES = 8_000_000
MAX_APPIDS_PER_BATCH = 10
_STEAM_APP_PATH = re.compile(r"^/app/([1-9][0-9]*)(?:/[^?#]*)?/?$")
_RAWG_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,180}$")
_BLOCK_TAGS = {"br", "p", "div", "li", "h1", "h2", "h3", "section"}


class MetadataProvider(Protocol):
    """Common async lookup contract consumed by the leased metadata worker."""

    request_reservation: int

    async def lookup_batch(self, appids: list[int]) -> dict[int, dict]: ...


class IGDBMetadataProvider:
    """Adapter that keeps the legacy IGDB client behind the same provider contract."""

    request_reservation = 6

    def __init__(self, client):
        self.client = client

    async def lookup_batch(self, appids: list[int]) -> dict[int, dict]:
        from playmatch.metadata_provider import lookup_batch

        return await lookup_batch(self.client, appids)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag.casefold() in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value: object, limit: int = 6000) -> str:
    if not isinstance(value, str):
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        return ""
    text = " ".join(" ".join(parser.parts).split())
    return html.unescape(text)[:limit].strip()


def _steam_id_from_url(value: object) -> int | None:
    if not isinstance(value, str) or len(value) > 1000:
        return None
    try:
        parsed = urlparse(value)
        match = _STEAM_APP_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "store.steampowered.com"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
            or not match
        ):
            return None
        appid = int(match.group(1))
        return appid if appid <= 4294967295 else None
    except ValueError:
        return None


def _rawg_store_ids(result: dict) -> set[int]:
    """Extract Steam AppIDs from either game-list links or /games/{id}/stores rows."""
    rows = result.get("stores")
    if not isinstance(rows, list):
        rows = result.get("results")
    if not isinstance(rows, list):
        return set()
    found = set()
    for item in rows[:30]:
        if not isinstance(item, dict):
            continue
        store = item.get("store")
        store_name = store.get("name", "") if isinstance(store, dict) else ""
        url = item.get("url")
        appid = _steam_id_from_url(url)
        if appid is not None and (not store_name or str(store_name).casefold() == "steam"):
            found.add(appid)
    return found


def _evidence(field: str, label: str, url: str, observed_at: str) -> dict:
    return {"field": field, "label": label, "url": url, "observed_at": observed_at}


def normalize_steam_app(
    raw: dict,
    appid: int,
    observed_at: str,
    *,
    rawg: dict | None = None,
) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict) or raw.get("steam_appid") != appid:
        return None, "identity_conflict"
    if str(raw.get("type", "")).casefold() != "game":
        return None, "not_verified_base_game"
    platforms = raw.get("platforms")
    if not isinstance(platforms, dict) or platforms.get("windows") is not True:
        return None, "pc_platform_missing"
    title = raw.get("name")
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        return None, "invalid_metadata"
    description = plain_text(raw.get("about_the_game")) or plain_text(
        raw.get("detailed_description")
    ) or plain_text(raw.get("short_description"))
    if not description:
        return None, "missing_description"

    store_url = f"https://store.steampowered.com/app/{appid}/"
    image_url = raw.get("header_image", "")
    if not isinstance(image_url, str) or len(image_url) > 1000:
        image_url = ""
    if image_url:
        try:
            parsed_image = urlparse(image_url)
            if (
                parsed_image.scheme != "https"
                or not parsed_image.hostname
                or not (
                    parsed_image.hostname.endswith(".steamstatic.com")
                    or parsed_image.hostname.endswith(".steamcontent.com")
                    or parsed_image.hostname == "steamcdn-a.akamaihd.net"
                )
                or parsed_image.username
                or parsed_image.password
            ):
                image_url = ""
        except ValueError:
            image_url = ""

    genres_raw = raw.get("genres", [])
    genres = [
        item["description"].strip()
        for item in genres_raw[:30]
        if isinstance(item, dict)
        and isinstance(item.get("description"), str)
        and item["description"].strip()
    ] if isinstance(genres_raw, list) else []
    categories_raw = raw.get("categories", [])
    categories = [
        item["description"].strip()
        for item in categories_raw[:60]
        if isinstance(item, dict)
        and isinstance(item.get("description"), str)
        and item["description"].strip()
    ] if isinstance(categories_raw, list) else []

    themes = list(dict.fromkeys([*genres, *categories]))
    evidence = [
        _evidence("metadata", "Steam Store AppDetails 游戏资料", store_url, observed_at),
        _evidence("description", "Steam Store 游戏简介原文清理版", store_url, observed_at),
        _evidence("themes", "Steam Store 类型与商店分类", store_url, observed_at),
        _evidence("platforms", "Steam Store 平台字段显示 Windows", store_url, observed_at),
    ]

    language_text = plain_text(raw.get("supported_languages", ""), limit=5000).casefold()
    chinese = "verified" if "simplified chinese" in language_text else "unknown"
    if chinese == "verified":
        evidence.append(
            _evidence(
                "chinese_support",
                "Steam Store 明确列出简体中文支持；商店语言声明，未独立验证安装包",
                store_url,
                observed_at,
            )
        )

    coop = "unknown"
    category_set = {value.casefold() for value in categories}
    if "online co-op" in category_set:
        coop = "online"
        evidence.append(_evidence("coop", "Steam Store 明确列出 Online Co-op", store_url, observed_at))
    elif category_set.intersection({"local co-op", "shared/split screen co-op"}):
        coop = "local"
        evidence.append(_evidence("coop", "Steam Store 明确列出本地合作分类", store_url, observed_at))

    horror = "yes" if any(value.casefold() == "horror" for value in genres) else "unknown"
    if horror == "yes":
        evidence.append(_evidence("horror", "Steam Store 明确列出 Horror 类型", store_url, observed_at))

    sources = []
    steam_hash = digest(
        {
            "appid": appid,
            "name": title,
            "description": description,
            "genres": genres,
            "categories": categories,
            "supported_languages": language_text,
            "platforms": platforms,
        }
    )
    sources.append(
        {
            "provider_id": f"steam-store:{appid}",
            "observed_at": observed_at,
            "content_hash": steam_hash,
        }
    )

    if rawg:
        rawg_id = rawg.get("id")
        slug = rawg.get("slug")
        if type(rawg_id) is int and rawg_id > 0 and isinstance(slug, str) and _RAWG_SLUG.fullmatch(slug):
            rawg_url = f"https://rawg.io/games/{slug}"
            rawg_genres = [
                item["name"].strip()
                for item in rawg.get("genres", [])[:30]
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item["name"].strip()
            ] if isinstance(rawg.get("genres"), list) else []
            rawg_tags = [
                item["name"].strip()
                for item in rawg.get("tags", [])[:60]
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item["name"].strip()
            ] if isinstance(rawg.get("tags"), list) else []
            genres = list(dict.fromkeys([*genres, *rawg_genres]))
            themes = list(dict.fromkeys([*themes, *rawg_genres, *rawg_tags]))
            if horror == "unknown" and any(v.casefold() == "horror" for v in rawg_tags):
                horror = "yes"
                evidence.append(
                    _evidence("horror", "RAWG 精确 Steam 身份匹配记录标注 Horror 标签", rawg_url, observed_at)
                )
            evidence.extend(
                [
                    _evidence("themes", "RAWG 补充类型与标签；已核验 Steam 商店链接身份", rawg_url, observed_at),
                    _evidence("metadata", "RAWG 补充游戏元数据；已核验 Steam 商店链接身份", rawg_url, observed_at),
                ]
            )
            sources.append(
                {
                    "provider_id": f"rawg:{rawg_id}",
                    "observed_at": observed_at,
                    "content_hash": digest(
                        {"id": rawg_id, "genres": rawg_genres, "tags": rawg_tags}
                    ),
                }
            )

    payload = Game.model_validate(
        {
            "id": f"steam-{appid}",
            "steam_app_id": appid,
            "title": title.strip(),
            "title_zh": title.strip(),
            "description": description,
            "themes": themes[:60],
            "genres": genres[:30],
            "platforms": ["PC"],
            "chinese_support": chinese,
            "coop": coop,
            "horror": horror,
            "image_url": image_url,
            "store_url": store_url,
            "evidence": evidence,
            "metadata_status": "provider",
            "estimated_hours": None,
        }
    ).model_dump()
    semantic = {
        **payload,
        "evidence": [{key: val for key, val in item.items() if key != "observed_at"} for item in evidence],
    }
    return {
        "payload": payload,
        "provider_id": f"steam-store:{appid}",
        "sources": sources,
        "updated_at": int(datetime.fromisoformat(observed_at).timestamp() * 1000),
        "hash": digest(semantic),
        "observed_at": observed_at,
    }, None


class SteamStoreClient:
    """Bounded best-effort client for Steam Store AppDetails (not an official Web API)."""

    def __init__(self, client: httpx.AsyncClient | None = None, *, timeout: float = 10.0):
        self._owned = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False, trust_env=False, timeout=timeout
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        if self._owned:
            await self._client.aclose()

    async def lookup_batch(self, appids: list[int]) -> dict[int, dict]:
        if not appids or len(appids) > MAX_APPIDS_PER_BATCH:
            raise ValueError("Steam Store batch must contain 1..10 AppIDs")
        if len(appids) != len(set(appids)) or any(
            type(appid) is not int or not 0 < appid <= 4294967295 for appid in appids
        ):
            raise ValueError("Invalid Steam AppID batch")
        try:
            response = await self._client.get(
                STEAM_APPDETAILS_URL,
                params={"appids": ",".join(str(value) for value in appids), "l": "english", "cc": "us"},
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
            if response.status_code == 429:
                raise ProviderRequestError("rate_limited")
            if response.status_code in {401, 403}:
                raise ProviderRequestError("authentication_failed")
            if response.status_code != 200:
                raise ProviderRequestError("provider_unavailable")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ProviderRequestError("response_too_large")
            payload = response.json()
        except ProviderRequestError:
            raise
        except httpx.HTTPError:
            raise ProviderRequestError("network_unavailable") from None
        except (ValueError, UnicodeDecodeError):
            raise ProviderRequestError("invalid_response") from None
        if not isinstance(payload, dict):
            raise ProviderRequestError("invalid_response")
        result = {}
        for appid in appids:
            entry = payload.get(str(appid))
            if not isinstance(entry, dict) or entry.get("success") is not True:
                result[appid] = {"error": "unmatched"}
                continue
            data = entry.get("data")
            if not isinstance(data, dict) or data.get("steam_appid") != appid:
                result[appid] = {"error": "identity_conflict"}
                continue
            result[appid] = {"data": data}
        return result


class RAWGClient:
    """Optional RAWG metadata supplement; only exact Steam store-link matches are accepted."""

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = 8.0,
        spacing_seconds: float = 0.30,
    ):
        self.api_key = api_key.strip()
        self._owned = client is None
        self._client = client or httpx.AsyncClient(follow_redirects=False, trust_env=False)
        self.timeout = timeout
        self.spacing_seconds = spacing_seconds
        self._gate = asyncio.Lock()
        self._next_request = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        if self._owned:
            await self._client.aclose()

    async def _get(self, path: str, params: dict) -> dict:
        async with self._gate:
            delay = max(0.0, self._next_request - time.monotonic())
            if delay:
                await asyncio.sleep(delay)
            self._next_request = time.monotonic() + self.spacing_seconds
        try:
            response = await self._client.get(
                f"{RAWG_BASE_URL}{path}",
                params={**params, "key": self.api_key},
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
            if response.status_code == 429:
                raise ProviderRequestError("rate_limited")
            if response.status_code in {401, 403}:
                raise ProviderRequestError("authentication_failed")
            if response.status_code != 200:
                raise ProviderRequestError("provider_unavailable")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ProviderRequestError("response_too_large")
            payload = response.json()
        except ProviderRequestError:
            raise
        except httpx.HTTPError:
            raise ProviderRequestError("network_unavailable") from None
        except (ValueError, UnicodeDecodeError):
            raise ProviderRequestError("invalid_response") from None
        if not isinstance(payload, dict):
            raise ProviderRequestError("invalid_response")
        return payload

    async def lookup_by_steam_id(self, appid: int, title: str) -> dict | None:
        if not self.configured:
            return None
        search = await self._get(
            "/games",
            {"search": title[:120], "search_exact": "true", "page_size": 10},
        )
        rows = search.get("results")
        if not isinstance(rows, list):
            return None
        matches = []
        # The RAWG games list does not guarantee store links in its summary objects.
        # Resolve store links through the documented /games/{id}/stores endpoint.
        for row in rows[:3]:
            if not isinstance(row, dict):
                continue
            rawg_id = row.get("id")
            if type(rawg_id) is not int or rawg_id <= 0:
                continue
            stores = await self._get(f"/games/{rawg_id}/stores", {"page_size": 20})
            if appid in _rawg_store_ids(stores):
                matches.append(rawg_id)
        if len(matches) != 1:
            return None
        detail = await self._get(f"/games/{matches[0]}", {})
        if detail.get("id") != matches[0]:
            return None
        return detail


class SteamRawgMetadataProvider:
    """Steam Store primary + optional RAWG supplement behind one provider contract."""

    def __init__(
        self,
        *,
        rawg_api_key: str = "",
        steam_client: SteamStoreClient | None = None,
        rawg_client: RAWGClient | None = None,
    ):
        self.steam = steam_client or SteamStoreClient()
        self.rawg = rawg_client or (RAWGClient(rawg_api_key) if rawg_api_key else None)
        self._owns_steam = steam_client is None
        self._owns_rawg = rawg_client is None and self.rawg is not None
        self.request_reservation = 1 + (50 if self.rawg and self.rawg.configured else 0)

    async def __aenter__(self):
        if self._owns_steam:
            await self.steam.__aenter__()
        if self._owns_rawg:
            await self.rawg.__aenter__()
        return self

    async def __aexit__(self, *_):
        if self._owns_steam:
            await self.steam.__aexit__()
        if self._owns_rawg:
            await self.rawg.__aexit__()

    async def lookup_batch(self, appids: list[int]) -> dict[int, dict]:
        steam_results = await self.steam.lookup_batch(appids)
        observed_at = datetime.now(UTC).isoformat()
        output = {}
        for appid in appids:
            entry = steam_results[appid]
            if entry.get("error"):
                output[appid] = entry
                continue
            data = entry["data"]
            supplement = None
            if self.rawg and self.rawg.configured:
                try:
                    supplement = await self.rawg.lookup_by_steam_id(appid, str(data.get("name", "")))
                except ProviderRequestError:
                    # RAWG is optional: preserve a valid Steam record when the supplement is unavailable.
                    supplement = None
            value, error = normalize_steam_app(data, appid, observed_at, rawg=supplement)
            output[appid] = {"error": error} if error else value
        return output
