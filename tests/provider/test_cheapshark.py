from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from playmatch.providers.cheapshark import CheapSharkClient, parse_retry_after, usd_to_minor


def deal(**overrides):
    row = {
        "steamAppID": "413150",
        "title": "Stardew Valley",
        "dealID": "abc%2Bdef%2Fghi%3D",
        "storeID": "1",
        "salePrice": "7.49",
        "normalPrice": "14.99",
        "lastChange": 1700000000,
    }
    row.update(overrides)
    return row


def transport_for(rows, *, stores_status=200):
    def handler(request):
        assert request.headers["user-agent"].startswith("PlayMatch/")
        if request.url.path.endswith("/stores"):
            return httpx.Response(stores_status, json=[{"storeID": "1", "storeName": "Steam"}])
        assert request.url.path.endswith("/deals")
        assert request.url.params["steamAppID"] == "413150"
        return httpx.Response(200, json=rows)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", 0),
        ("0.00", 0),
        ("0.01", 1),
        ("19.99", 1999),
        ("7.490", 749),
        (Decimal("12.34"), 1234),
        (0.1, 10),
        (15, 1500),
    ],
)
def test_exact_usd_amounts(value, expected):
    assert usd_to_minor(value) == expected


@pytest.mark.parametrize(
    "value", [None, True, "NaN", "Infinity", "-0.01", "0.001", "1,99", "$7.49", {}, "1000001"]
)
def test_invalid_money_is_rejected(value):
    with pytest.raises(ValueError):
        usd_to_minor(value)


def test_retry_after_dates_and_invalid_values():
    now = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert parse_retry_after("Tue, 20 Oct 2026 10:00:03 GMT", now=now) == 3.0
    assert parse_retry_after("0.5") == 0.5
    assert parse_retry_after("NaN") is None
    assert parse_retry_after("-1") is None
    assert parse_retry_after("tomorrow") is None


async def test_cn_is_not_usd_conversion_and_makes_no_network_call():
    def forbidden(_):
        raise AssertionError("unsupported region must not query upstream")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        result = await CheapSharkClient(http).get_offers(413150, "CN")
    assert result["supported"] is False
    assert result["currency"] is None
    assert result["status"] == "unsupported"
    assert result["offers"] == []


async def test_unknown_activation_never_becomes_guaranteed_best_and_links_preserved():
    async with httpx.AsyncClient(transport=transport_for([deal()])) as http:
        result = await CheapSharkClient(http).get_offers(413150)
    assert result["status"] == "partial"
    assert result["best_offer_id"] is None
    offer = result["offers"][0]
    assert offer["price_minor"] == 749
    assert offer["currency"] == "USD"
    assert offer["store"] == "Steam"
    assert offer["activation_status"] == "unknown"
    assert offer["eligible_for_best"] is False
    parsed = urlparse(offer["url"])
    assert parsed.netloc == "www.cheapshark.com"
    assert parsed.path == "/redirect"
    assert parse_qs(parsed.query)["dealID"] == ["abc+def/ghi="]
    assert offer["fetched_at"] != offer["source_updated_at"]


async def test_rejects_wrong_game_dlc_bad_money_and_duplicates():
    rows = [
        deal(),
        deal(),
        deal(steamAppID="620"),
        deal(title="Stardew Valley Soundtrack"),
        deal(dealID="invalid", salePrice="NaN"),
        deal(dealID="fractions", salePrice="1.001"),
        deal(dealID="expensive", salePrice="999"),
        deal(dealID="dlc", title="Stardew Valley DLC"),
    ]
    async with httpx.AsyncClient(transport=transport_for(rows)) as http:
        result = await CheapSharkClient(http).get_offers(413150)
    assert len(result["offers"]) == 1
    assert any("已排除 6 条" in n for n in result["notices"])


async def test_empty_quotes_are_not_free_or_out_of_stock():
    async with httpx.AsyncClient(transport=transport_for([])) as http:
        result = await CheapSharkClient(http).get_offers(413150)
    assert result["status"] == "ok"
    assert result["offers"] == []
    assert result["best_offer_id"] is None


async def test_explicit_mismatched_currency_or_country_is_not_relabelled():
    rows = [deal(currency="CNY"), deal(dealID="different-region", country="CN")]
    async with httpx.AsyncClient(transport=transport_for(rows)) as http:
        result = await CheapSharkClient(http).get_offers(413150)
    assert result["offers"] == []
    assert result["status"] == "partial"
    assert result["best_offer_id"] is None


async def test_malformed_response_and_store_failure_degrade_safely():
    async with httpx.AsyncClient(transport=transport_for({"error": "oops"})) as http:
        assert (await CheapSharkClient(http).get_offers(413150))["status"] == "unavailable"
    async with httpx.AsyncClient(transport=transport_for([deal()], stores_status=403)) as http:
        result = await CheapSharkClient(http).get_offers(413150)
    assert result["status"] == "partial"
    assert result["offers"][0]["store"] == "CheapShark 店铺 1"


async def test_short_retry_after_is_respected_and_bounded():
    attempts = 0
    sleeps = []

    async def no_wait(delay):
        sleeps.append(delay)

    def handler(request):
        nonlocal attempts
        if request.url.path.endswith("/stores"):
            return httpx.Response(200, json=[{"storeID": "1", "storeName": "Steam"}])
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.5"})
        return httpx.Response(200, json=[deal()])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await CheapSharkClient(http, sleep=no_wait).get_offers(413150)
    assert result["offers"]
    assert attempts == 2
    assert sleeps == [0.5]


async def test_long_retry_after_stops_without_retrying_early():
    attempts = 0

    async def forbidden_sleep(_):
        raise AssertionError("must not wait or retry before long Retry-After")

    def handler(_):
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": "120"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await CheapSharkClient(http, sleep=forbidden_sleep).get_offers(413150)
    assert result["status"] == "unavailable"
    assert attempts == 1


async def test_timeout_is_sanitized_and_no_price_is_invented():
    def handler(request):
        raise httpx.ReadTimeout("private upstream diagnostic", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await CheapSharkClient(http).get_offers(413150)
    assert result["offers"] == []
    assert result["status"] == "unavailable"
    assert "private upstream" not in str(result)


async def test_redirects_are_not_followed():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://example.org/unsafe"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as http:
        result = await CheapSharkClient(http).get_offers(413150)
    assert result["status"] == "unavailable"
    assert len(calls) == 1


async def test_provider_does_not_close_injected_client():
    async with httpx.AsyncClient(transport=transport_for([])) as http:
        async with CheapSharkClient(http) as provider:
            await provider.get_offers(413150)
        assert not http.is_closed
