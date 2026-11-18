"""Opt-in metadata enrichment worker. Credentials stay server-side and are never logged."""

import argparse
import asyncio
import logging

from sqlalchemy.exc import SQLAlchemyError

from playmatch.config import Settings
from playmatch.database import Database
from playmatch.metadata_enrichment import EnrichmentLeaseLost, MetadataEnricher
from playmatch.providers.igdb import IGDBClient
from playmatch.providers.metadata_sources import IGDBMetadataProvider, SteamRawgMetadataProvider


async def run(settings, once):
    db = Database(settings.database_url)
    try:
        if settings.metadata_provider == "igdb":
            async with IGDBClient(
                client_id=settings.igdb_client_id,
                client_secret=settings.igdb_client_secret,
            ) as client:
                provider = IGDBMetadataProvider(client)
                worker = MetadataEnricher(db, settings, provider)
                await _run_worker(worker, once)
        else:
            async with SteamRawgMetadataProvider(
                rawg_api_key=settings.rawg_api_key.get_secret_value()
            ) as provider:
                worker = MetadataEnricher(db, settings, provider)
                await _run_worker(worker, once)
    finally:
        db.close()


async def _run_worker(worker, once):
    while True:
        try:
            await worker.tick()
        except (SQLAlchemyError, EnrichmentLeaseLost):
            logging.warning("metadata_iteration_unavailable")
        if once:
            break
        await asyncio.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Process one due batch; never bypass quotas"
    )
    args = parser.parse_args()
    settings = Settings()
    if not settings.metadata_enrichment_enabled or not settings.metadata_configured:
        parser.exit(2, "Metadata enrichment disabled or provider credentials missing.\n")
    try:
        asyncio.run(run(settings, args.once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
