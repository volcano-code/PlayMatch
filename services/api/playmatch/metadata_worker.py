"""Opt-in automatic IGDB enrichment worker. No schema migration or credential output."""

import argparse
import asyncio
import logging

from sqlalchemy.exc import SQLAlchemyError

from playmatch.config import Settings
from playmatch.database import Database
from playmatch.metadata_enrichment import EnrichmentLeaseLost, MetadataEnricher
from playmatch.providers.igdb import IGDBClient


async def run(settings, once):
    db = Database(settings.database_url)
    try:
        async with IGDBClient(
            client_id=settings.igdb_client_id, client_secret=settings.igdb_client_secret
        ) as client:
            worker = MetadataEnricher(db, settings, client)
            while True:
                try:
                    await worker.tick()
                except (SQLAlchemyError, EnrichmentLeaseLost):
                    logging.warning("metadata_iteration_unavailable")
                if once:
                    break
                await asyncio.sleep(2)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Process one due batch; never bypass quotas"
    )
    args = parser.parse_args()
    settings = Settings()
    if not settings.metadata_enrichment_enabled or not settings.metadata_configured:
        parser.exit(2, "Metadata enrichment disabled or credentials missing.\n")
    try:
        asyncio.run(run(settings, args.once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
