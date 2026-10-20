"""Optional server-side IGDB integration; credentials never go in URLs.

This adapter does not silently replace the reviewed starter catalog. Importing
provider records and verifying per-field coverage is a separate release step.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from .cheapshark import MAX_RESPONSE_BYTES, ProviderRequestError, parse_retry_after

API_URL = "https://api.igdb.com/v4/games"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_STEAM_PATH = re.compile(r"^/app/(\d+)(?:/|$)")


class IGDBClient:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float = 6.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client_id = (
            client_id if client_id is not None else os.getenv("IGDB_CLIENT_ID", "")
        ).strip()
        self._client_secret = (
            client_secret if client_secret is not None else os.getenv("IGDB_CLIENT_SECRET", "")
        ).strip()
        self._owned = client is None
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self.timeout = max(0.1, min(timeout, 15.0))
        self._sleep = sleep
        self._token = ""
        self._token_expires = 0.0
        self._token_lock = asyncio.Lock()
        self._rate_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(8)
        self._next_request = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def health(self) -> dict[str, Any]:
        return {
            "provider": "igdb",
            "status": "configured_unprobed" if self.configured else "not_configured",
            "catalog_import_enabled": False,
        }

    async def __aenter__(self) -> IGDBClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._token = ""
        if self._owned:
            await self._client.aclose()

    async def _get_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires:
                return self._token
            try:
                response = await self._client.post(
                    TOKEN_URL,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "grant_type": "client_credentials",
                    },
                    timeout=self.timeout,
                    follow_redirects=False,
                )
                if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
                    raise ProviderRequestError("authentication_failed")
                payload = response.json()
                token = payload.get("access_token") if isinstance(payload, dict) else None
                expires = payload.get("expires_in") if isinstance(payload, dict) else None
                if (
                    not isinstance(token, str)
                    or not token
                    or not isinstance(expires, int)
                    or isinstance(expires, bool)
                    or expires <= 0
                ):
                    raise ProviderRequestError("invalid_token_response")
                self._token = token
                self._token_expires = time.monotonic() + max(0, expires - min(60, expires / 10))
                return token
            except httpx.HTTPError as exc:
                raise ProviderRequestError("authentication_unavailable") from exc
            except (ValueError, UnicodeDecodeError) as exc:
                raise ProviderRequestError("invalid_token_response") from exc

    async def _rate_gate(self) -> None:
        # One shared client is required per process. Start spacing > 250ms keeps
        # calls below four per second; the semaphore enforces at most eight open.
        async with self._rate_lock:
            delay = max(0.0, self._next_request - time.monotonic())
            if delay:
                await self._sleep(delay)
            self._next_request = time.monotonic() + 0.26

    async def _query(self, body: str) -> list[dict[str, Any]]:
        refreshed = False
        for attempt in range(3):
            token = await self._get_token()
            async with self._semaphore:
                await self._rate_gate()
                try:
                    response = await self._client.post(
                        API_URL,
                        content=body.encode("utf-8"),
                        headers={
                            "Client-ID": self._client_id,
                            "Authorization": "Bearer " + token,
                            "Accept": "application/json",
                            "Content-Type": "text/plain",
                        },
                        timeout=self.timeout,
                        follow_redirects=False,
                    )
                except httpx.HTTPError as exc:
                    raise ProviderRequestError("network_unavailable") from exc
            if response.status_code == 401 and not refreshed:
                # Invalidate only the token used by this call; another concurrent
                # call may already have refreshed it.
                async with self._token_lock:
                    if self._token == token:
                        self._token = ""
                        self._token_expires = 0.0
                refreshed = True
                continue
            if response.status_code == 429 and attempt < 2:
                wait = parse_retry_after(response.headers.get("Retry-After"))
                wait = 0.5 if wait is None else wait
                if wait > 1.0:
                    raise ProviderRequestError("rate_limited")
                await self._sleep(wait)
                continue
            if response.status_code != 200:
                raise ProviderRequestError("upstream_unavailable")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ProviderRequestError("response_too_large")
            try:
                rows = response.json()
            except (ValueError, UnicodeDecodeError) as exc:
                raise ProviderRequestError("invalid_json") from exc
            if not isinstance(rows, list):
                raise ProviderRequestError("invalid_response")
            return [row for row in rows if isinstance(row, dict)]
        raise ProviderRequestError("upstream_unavailable")

    async def search_games(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        if not self.configured:
            return {
                "status": "not_configured",
                "items": [],
                "notices": [
                    "尚未配置 IGDB_CLIENT_ID / IGDB_CLIENT_SECRET；继续使用人工整理的启动目录。"
                ],
            }
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 200
            or any(ord(c) < 32 for c in query)
        ):
            return {
                "status": "unavailable",
                "items": [],
                "notices": ["搜索词须为 1–200 个可显示字符。"],
            }
        if isinstance(limit, bool) or not isinstance(limit, int):
            return {"status": "unavailable", "items": [], "notices": ["查询条数必须为整数。"]}
        limit = max(1, min(limit, 50))
        escaped = query.strip().replace("\\", "\\\\").replace('"', '\\"')
        body = (
            "fields id,name,summary,url,genres.name,themes.name,platforms.name,cover.url,"
            "external_games.uid,external_games.url,external_games.external_game_source.name; "
            f'search "{escaped}"; limit {limit};'
        )
        try:
            rows = await self._query(body)
        except ProviderRequestError:
            return {
                "status": "unavailable",
                "items": [],
                "notices": ["IGDB 暂时不可用；没有回传凭证、访问令牌或供应商响应正文。"],
            }
        items = [_parse_game(row) for row in rows[:limit]]
        return {
            "status": "ok",
            "items": [item for item in items if item],
            "notices": ["供应商记录尚未导入启动目录；语言、联机与地区可购买性需分别核验。"],
        }


def _names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item["name"].strip()
        for item in value
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()
    ]


def _parsed_url(value: object):
    if not isinstance(value, str):
        return None
    try:
        return urlparse(value)
    except ValueError:
        return None


def _parse_game(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row.get("id"), int) or isinstance(row["id"], bool) or row["id"] <= 0:
        return None
    if not isinstance(row.get("name"), str) or not row["name"].strip():
        return None
    steam_app_id: int | None = None
    external = row.get("external_games", [])
    if isinstance(external, list):
        for game in external:
            if not isinstance(game, dict):
                continue
            parsed = _parsed_url(game.get("url", ""))
            match = _STEAM_PATH.match(parsed.path) if parsed else None
            if (
                parsed
                and parsed.scheme in {"http", "https"}
                and parsed.hostname == "store.steampowered.com"
                and match
            ):
                steam_app_id = int(match.group(1))
                break
            source = game.get("external_game_source")
            if (
                isinstance(source, dict)
                and str(source.get("name", "")).lower() == "steam"
                and str(game.get("uid", "")).isdigit()
            ):
                steam_app_id = int(game["uid"])
                break
    cover = row.get("cover")
    image_url = cover.get("url", "") if isinstance(cover, dict) else ""
    if not isinstance(image_url, str):
        image_url = ""
    if image_url.startswith("//"):
        image_url = "https:" + image_url
    parsed_image = _parsed_url(image_url)
    if image_url and (
        not parsed_image
        or parsed_image.scheme != "https"
        or parsed_image.hostname != "images.igdb.com"
    ):
        image_url = ""
    source_url = row.get("url", "")
    parsed_source = _parsed_url(source_url)
    if (
        not parsed_source
        or parsed_source.scheme != "https"
        or parsed_source.hostname not in {"igdb.com", "www.igdb.com"}
    ):
        # Numeric IGDB ids are not website slugs; do not invent a /games/{id} link.
        source_url = API_URL
    return {
        "provider_id": str(row["id"]),
        "steam_app_id": steam_app_id,
        "title": row["name"].strip(),
        "description": row.get("summary", "") if isinstance(row.get("summary", ""), str) else "",
        "themes": _names(row.get("themes")),
        "genres": _names(row.get("genres")),
        "platforms": _names(row.get("platforms")),
        "image_url": image_url,
        "source_url": source_url,
    }
