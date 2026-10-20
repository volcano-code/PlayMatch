#!/usr/bin/env python3
"""Fetch bounded IGDB pages into a review file; no database changes. Requires --live."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))

from playmatch.catalog_collect import collect_catalog, write_review  # noqa: E402
from playmatch.config import Settings  # noqa: E402
from playmatch.providers.cheapshark import ProviderRequestError  # noqa: E402
from playmatch.providers.igdb import IGDBClient  # noqa: E402


async def collect(args):
    settings = Settings()
    async with IGDBClient(
        client_id=settings.igdb_client_id, client_secret=settings.igdb_client_secret
    ) as client:
        return await collect_catalog(
            client, after_id=args.after_id, pages=args.pages, page_size=args.page_size
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=50)
    args = parser.parse_args()
    if not args.live:
        parser.error("Use --live to explicitly authorize the external IGDB calls")
    try:
        if args.output.exists():
            raise ValueError("Review already exists")
        result = asyncio.run(collect(args))
        write_review(args.output, result)
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "review_sha256",
                        "scanned",
                        "accepted",
                        "next_after_id",
                        "source_exhausted",
                    )
                },
                indent=2,
            )
        )
        return 0
    except (ValueError, OSError, ProviderRequestError, TimeoutError):
        print(
            "Collection failed or is unconfigured. No complete review was published; no catalog was changed. Check credentials, input and source availability.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
