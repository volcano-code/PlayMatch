#!/usr/bin/env python3
"""Explicit, bounded, read-only provider probe. No alerts or purchase requests.

Run from the repository: uv run python scripts/probe_providers.py --live
The default invocation performs no network activity. The optional output only
records diagnostic counts/statuses, not a copied catalog of provider prices.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from playmatch.providers.cheapshark import CheapSharkClient  # noqa: E402
from playmatch.providers.igdb import IGDBClient  # noqa: E402


async def probe(args: argparse.Namespace) -> tuple[dict, int]:
    report = {
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "live": args.live,
        "purpose": "Explicit user-triggered read-only provider validation; no alerts or purchases.",
        "cheapshark": [],
        "igdb": {"status": "not_probed"},
    }
    exit_code = 0
    async with IGDBClient() as igdb:
        report["igdb"] = igdb.health()
        if args.live and args.igdb_search:
            result = await igdb.search_games(args.igdb_search, limit=3)
            report["igdb"] = {
                "status": result["status"],
                "item_count": len(result["items"]),
                "notices": result["notices"],
                "catalog_import_enabled": False,
            }
    if not args.live:
        report["notice"] = (
            "No network calls made. Use --live for at most three CheapShark game lookups."
        )
        return report, exit_code
    async with CheapSharkClient(timeout=6, max_retries=1) as provider:
        for app_id in args.app_id or [413150, 620]:
            start = time.monotonic()
            result = await provider.get_offers(app_id, country=args.country)
            report["cheapshark"].append(
                {
                    "steam_app_id": app_id,
                    "country": result["country"],
                    "currency": result["currency"],
                    "status": result["status"],
                    "supported": result["supported"],
                    "offer_count": len(result["offers"]),
                    "eligible_for_best_count": sum(
                        o["eligible_for_best"] for o in result["offers"]
                    ),
                    "best_offer_id": result["best_offer_id"],
                    "fetched_at": result["fetched_at"],
                    "elapsed_ms": round((time.monotonic() - start) * 1000),
                    "notices": result["notices"],
                }
            )
            if result["status"] == "unavailable":
                exit_code = 2
    return report, exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly permit a small read-only live probe"
    )
    parser.add_argument("--app-id", type=int, action="append", help="Steam AppID; maximum three")
    parser.add_argument("--country", choices=["US", "CN"], default="US")
    parser.add_argument(
        "--igdb-search", help="Optional query, only with configured credentials and --live"
    )
    parser.add_argument("--output", type=Path, help="Save the diagnostic JSON report")
    args = parser.parse_args()
    if args.app_id and (len(args.app_id) > 3 or any(i <= 0 for i in args.app_id)):
        parser.error("Supply at most three positive app IDs")
    report, exit_code = asyncio.run(probe(args))
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
