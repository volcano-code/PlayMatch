"""User-triggered CheapShark queries, with no background catalog harvesting.

CheapShark returns observed USD quotes, not verified regional activation rights.
Consequently this adapter never labels an offer as a guaranteed eligible best buy.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, unquote

import httpx

BASE_URL = "https://www.cheapshark.com/api/1.0"
USER_AGENT = "PlayMatch/0.1 (user-triggered game price comparison)"
MAX_RESPONSE_BYTES = 2_000_000
_NON_GAME = re.compile(r"\b(dlc|soundtrack|season\s+pass|upgrade|art\s?book)\b", re.I)


class ProviderRequestError(Exception):
    """A sanitized provider failure; never includes response bodies or secrets."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def usd_to_minor(value: object) -> int:
    """Parse exact USD cents; reject malformed, negative or fractional cents."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("invalid USD amount")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid USD amount") from exc
    if not amount.is_finite() or amount < 0 or amount > 1_000_000:
        raise ValueError("invalid USD amount")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValueError("USD amount has fractional cents")
    return int(cents)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Accept both RFC HTTP dates and numeric seconds; never wait unboundedly."""
    if not value:
        return None
    try:
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0.0, (retry_at - (now or datetime.now(UTC))).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _source_time(value: object) -> str | None:
    try:
        if isinstance(value, bool) or value is None:
            return None
        stamp = int(str(value))
        if stamp <= 0:
            return None
        return datetime.fromtimestamp(stamp, UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _result(
    country: str,
    *,
    status: str,
    supported: bool = True,
    offers: list[dict[str, Any]] | None = None,
    notices: list[str] | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    return {
        "supported": supported,
        "country": country,
        "currency": "USD" if supported else None,
        "offers": offers or [],
        "best_offer_id": None,
        "fetched_at": fetched_at,
        "status": status,
        "notices": notices or [],
    }


class CheapSharkClient:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = 6.0,
        max_retries: int = 1,
        retry_budget_seconds: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._owned = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        )
        self.timeout = max(0.1, min(timeout, 15.0))
        self.max_retries = max(0, min(max_retries, 2))
        self.retry_budget = max(0.0, min(retry_budget_seconds, 5.0))
        self._sleep = sleep
        self._stores: dict[str, str] = {}
        self._stores_at = 0.0

    async def __aenter__(self) -> CheapSharkClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    async def _request_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        slept = 0.0
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.get(
                    BASE_URL + path,
                    params=params,
                    timeout=self.timeout,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    follow_redirects=False,
                )
            except httpx.TimeoutException as exc:
                raise ProviderRequestError("timeout") from exc
            except httpx.HTTPError as exc:
                raise ProviderRequestError("network_error") from exc
            if response.status_code == 429 or response.status_code >= 500:
                code = "rate_limited" if response.status_code == 429 else "upstream_error"
                wait = parse_retry_after(response.headers.get("Retry-After"))
                wait = wait if wait is not None else 0.25 * (2**attempt)
                # A long Retry-After means stop, not retry earlier than requested.
                if attempt >= self.max_retries or slept + wait > self.retry_budget:
                    raise ProviderRequestError(code)
                await self._sleep(wait)
                slept += wait
                continue
            if response.status_code != 200:
                raise ProviderRequestError("unexpected_http_status")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ProviderRequestError("response_too_large")
            try:
                return response.json()
            except (ValueError, UnicodeDecodeError) as exc:
                raise ProviderRequestError("invalid_json") from exc
        raise ProviderRequestError("upstream_error")

    async def _load_stores(self) -> bool:
        # This tiny directory is fetched only during a user price query.
        if self._stores and time.monotonic() - self._stores_at < 86400:
            return True
        try:
            payload = await self._request_json("/stores")
            if not isinstance(payload, list):
                return False
            stores = {
                str(row["storeID"]): row["storeName"].strip()
                for row in payload
                if isinstance(row, dict)
                and str(row.get("storeID", "")).isdigit()
                and isinstance(row.get("storeName"), str)
                and row["storeName"].strip()
            }
            if not stores:
                return False
            self._stores = stores
            self._stores_at = time.monotonic()
            return True
        except ProviderRequestError:
            return False

    async def get_offers(self, steam_app_id: int, country: str = "US") -> dict[str, Any]:
        country = country.strip().upper()
        if country != "US":
            return _result(
                country,
                status="unsupported",
                supported=False,
                notices=[
                    "当前报价来源仅启用 US / USD 范围；未提供此地区的可核验报价。美元换算不能视为中国区售价。"
                ],
            )
        if isinstance(steam_app_id, bool) or not isinstance(steam_app_id, int) or steam_app_id <= 0:
            return _result(country, status="unavailable", notices=["无效的 Steam 游戏标识。"])
        try:
            rows = await self._request_json(
                "/deals",
                {
                    "steamAppID": steam_app_id,
                    "pageSize": 60,
                    "pageNumber": 0,
                    "sortBy": "Price",
                    "desc": 0,
                },
            )
        except ProviderRequestError as exc:
            messages = {
                "timeout": "报价服务请求超时，请稍后重试。",
                "rate_limited": "报价来源正在限流；本次查询已停止重试，请稍后再试。",
            }
            return _result(
                country,
                status="unavailable",
                notices=[messages.get(exc.code, "暂时无法获取可核验报价；没有使用模型生成价格。")],
            )
        if not isinstance(rows, list):
            return _result(
                country, status="unavailable", notices=["报价来源返回了无法识别的数据格式。"]
            )
        fetched_at = _iso_now()
        if not rows:
            return _result(
                country,
                status="ok",
                fetched_at=fetched_at,
                notices=["报价来源当前没有此游戏的可用记录；这不代表游戏免费或已下架。"],
            )
        stores_ok = await self._load_stores()
        offers: list[dict[str, Any]] = []
        skipped = 0
        seen: set[str] = set()
        for row in rows[:60]:
            if not isinstance(row, dict):
                skipped += 1
                continue
            title, deal_id = row.get("title"), row.get("dealID")
            store_id = str(row.get("storeID", ""))
            if (
                str(row.get("steamAppID")) != str(steam_app_id)
                or ("currency" in row and row["currency"] != "USD")
                or ("country" in row and row["country"] != "US")
                or not isinstance(title, str)
                or not title.strip()
                or _NON_GAME.search(title)
                or not isinstance(deal_id, str)
                or not 1 <= len(deal_id) <= 2048
                or not store_id.isdigit()
            ):
                skipped += 1
                continue
            canonical_deal = unquote(deal_id)
            if canonical_deal in seen:
                continue
            try:
                price = usd_to_minor(row.get("salePrice"))
                regular = usd_to_minor(row.get("normalPrice"))
                if price > regular:
                    raise ValueError("sale price exceeds regular price")
            except ValueError:
                skipped += 1
                continue
            seen.add(canonical_deal)
            offers.append(
                {
                    "id": "cheapshark:" + canonical_deal,
                    "store": self._stores.get(store_id, f"CheapShark 店铺 {store_id}"),
                    "price_minor": price,
                    "regular_price_minor": regular,
                    "currency": "USD",
                    "url": "https://www.cheapshark.com/redirect?dealID="
                    + quote(canonical_deal, safe=""),
                    "country": "US",
                    "fetched_at": fetched_at,
                    "source_updated_at": _source_time(row.get("lastChange")),
                    "edition": title.strip(),
                    "activation_status": "unknown",
                    "eligible_for_best": False,
                    "provider": "cheapshark",
                }
            )
        offers.sort(key=lambda item: (item["price_minor"], item["store"], item["id"]))
        notices = [
            "以下为 CheapShark 观测到的美元报价；版本与地区激活资格尚未核验，因此不标记保证可购买的最低价。",
            "购买前请在商店确认版本、适用地区与最终结算价；供应商变更时间不等于本次抓取时间。",
        ]
        if skipped:
            notices.append(f"已排除 {skipped} 条标识不匹配、非本体或金额格式异常的记录。")
        if not stores_ok:
            notices.append("部分商店名称暂不可用，使用供应商商店标识展示。")
        if len(rows) >= 60:
            notices.append("本次仅展示最多 60 条来源记录，不声称覆盖全部商店报价。")
        if not offers:
            notices.append("没有通过基础校验的报价；无法据此作出价格结论。")
        return _result(
            country, status="partial", offers=offers, notices=notices, fetched_at=fetched_at
        )
