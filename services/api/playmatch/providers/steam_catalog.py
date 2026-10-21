"""Read-only official Steam Store directory. No prices, HTML scraping or client keys.

The configured key is sent only in x-webapi-key, never in URLs or public errors.
Response/elapsed/page bounds and cursor validation apply before any database write.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
MAX_BYTES = 8_000_000
MAX_APPID = 2**32 - 1


class SteamCatalogError(ValueError):
    def __init__(self, code: str, retry_after: float = 60):
        super().__init__(code)
        self.code = code
        self.retry_after = max(1, min(retry_after, 86400))


@dataclass(frozen=True)
class SteamPage:
    apps: list[dict]
    cursor: int
    more: bool
    digest: str


def uint(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int or not 0 <= value <= MAX_APPID:
        raise SteamCatalogError("invalid_response")
    return value


def parse_page(payload, cursor: int, limit: int) -> SteamPage:
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
        raise SteamCatalogError("invalid_response")
    response = payload["response"]
    # Protobuf JSON can omit an empty repeated field. Omission alone is not EOF.
    apps = response.get("apps", [] if response.get("have_more_results") is False else None)
    if not isinstance(apps, list) or len(apps) > limit:
        raise SteamCatalogError("invalid_response")
    rows, previous = [], cursor
    for app in apps:
        if not isinstance(app, dict):
            raise SteamCatalogError("invalid_response")
        appid = uint(app.get("appid"))
        name = app.get("name")
        if appid <= previous:
            raise SteamCatalogError("non_advancing_cursor")
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 1024
            or not name.isprintable()
        ):
            raise SteamCatalogError("invalid_name")
        rows.append(
            {
                "appid": appid,
                "name": name.strip(),
                "last_modified": uint(app.get("last_modified"), nullable=True),
                "price_change_number": uint(app.get("price_change_number"), nullable=True),
            }
        )
        previous = appid
    more = response.get("have_more_results")
    if more is not None and type(more) is not bool:
        raise SteamCatalogError("invalid_response")
    # Without an explicit end flag, read one additional page, even for short pages.
    more = bool(rows) if more is None else more
    if more and not rows:
        raise SteamCatalogError("non_advancing_cursor")
    if "last_appid" in response and uint(response["last_appid"]) != previous:
        raise SteamCatalogError("cursor_mismatch")
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return SteamPage(rows, previous, more, hashlib.sha256(canonical.encode()).hexdigest())


def retry_delay(value: str | None) -> float:
    if not value:
        return 60
    try:
        if value.isdigit():
            return min(float(value), 86400)
        date = parsedate_to_datetime(value)
        return min(max(1, date.timestamp() - datetime.now(UTC).timestamp()), 86400)
    except (TypeError, ValueError, OverflowError):
        return 60


class SteamCatalogClient:
    def __init__(self, key: str, *, transport=None):
        self._key = key
        self._transport = transport

    def page(self, cursor: int, since: int, limit: int) -> SteamPage:
        if not self._key.strip():
            raise SteamCatalogError("not_configured", 3600)
        uint(cursor)
        uint(since)
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise SteamCatalogError("invalid_page_size")
        parameters = {
            "include_games": True,
            "include_dlc": False,
            "include_software": False,
            "include_videos": False,
            "include_hardware": False,
            "last_appid": cursor,
            "max_results": limit,
            "if_modified_since": since,
        }
        deadline = time.monotonic() + 25
        try:
            with httpx.Client(
                timeout=15, follow_redirects=False, trust_env=False, transport=self._transport
            ) as client:
                with client.stream(
                    "GET",
                    URL,
                    params={"input_json": json.dumps(parameters), "format": "json"},
                    headers={"x-webapi-key": self._key, "Accept": "application/json"},
                ) as response:
                    if response.status_code in (401, 403):
                        raise SteamCatalogError("authentication_failed", 3600)
                    if response.status_code == 429:
                        raise SteamCatalogError(
                            "rate_limited", retry_delay(response.headers.get("Retry-After"))
                        )
                    if response.status_code != 200:
                        raise SteamCatalogError("upstream_unavailable")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES or time.monotonic() > deadline:
                            raise SteamCatalogError("response_limit")
            return parse_page(json.loads(body), cursor, limit)
        except SteamCatalogError:
            raise
        except httpx.HTTPError:
            raise SteamCatalogError("network_unavailable") from None
        except (ValueError, UnicodeDecodeError):
            raise SteamCatalogError("invalid_json") from None
