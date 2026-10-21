"""Browser-only provider fixture; never selected by production entrypoints.

No fake price HTTP endpoint is exposed. Only this test launcher injects a provider
implementation; an explicit flag and the disposable e2e database are required.
"""

import os
from datetime import UTC, datetime

if os.getenv("PLAYMATCH_E2E_FIXTURES") != "1" or not os.getenv("DATABASE_URL", "").endswith(
    "/.state/e2e.db"
):
    raise RuntimeError("This launcher requires explicit disposable browser-test configuration")

from playmatch.main import app  # noqa: E402, F401
from playmatch.providers.cheapshark import CheapSharkClient  # noqa: E402


async def fixture_offers(self, steam_app_id: int, country: str = "US"):
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if country != "US":
        return {
            "supported": False,
            "country": country,
            "currency": None,
            "offers": [],
            "best_offer_id": None,
            "fetched_at": None,
            "status": "unsupported",
            "notices": ["国区价格暂未接入；浏览器测试不会请求真实价格源。"],
        }
    return {
        "supported": True,
        "country": "US",
        "currency": "USD",
        "status": "partial",
        "best_offer_id": None,
        "fetched_at": now,
        "notices": ["合成报价，仅供端到端测试，不是真实优惠。"],
        "offers": [
            {
                "id": "e2e-synthetic-quote",
                "store": "Synthetic E2E Store",
                "price_minor": 799,
                "regular_price_minor": 1999,
                "currency": "USD",
                "country": "US",
                "fetched_at": now,
                "source_updated_at": None,
                "url": "https://www.cheapshark.com/redirect?dealID=e2e-synthetic",
                "edition": "unknown",
                "activation_status": "unknown",
                "eligible_for_best": False,
                "provider": "cheapshark",
            }
        ],
    }


CheapSharkClient.get_offers = fixture_offers
