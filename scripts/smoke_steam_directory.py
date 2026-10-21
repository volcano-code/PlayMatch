"""Explicit disposable-container synthetic discovery fixture, not live Steam data."""

import os

import httpx
from playmatch.config import Settings
from playmatch.database import Database
from playmatch.providers.steam_catalog import parse_page
from playmatch.steam_sync import SteamSynchronizer


def main():
    if os.getenv("PLAYMATCH_ALLOW_STEAM_FIXTURE") != "yes":
        raise SystemExit("Refusing synthetic writes without explicit disposable test flag")
    settings = Settings(steam_sync_enabled=True, steam_web_api_key="SYNTHETIC_NOT_LIVE")
    db = Database(settings.database_url)
    try:
        worker = SteamSynchronizer(db, settings)
        claim = worker.claim()
        assert claim and claim.cursor == 0
        worker.apply(
            claim,
            parse_page(
                {
                    "response": {
                        "apps": [{"appid": 4000000000, "name": "SYNTHETIC CONTAINER DISCOVERY"}],
                        "have_more_results": False,
                    }
                },
                0,
                5000,
            ),
        )
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
            page = client.get("/api/v1/catalog/steam/games?q=4000000000")
            page.raise_for_status()
            game = page.json()["items"][0]
            assert game["metadata_status"] == "pending" and game["game_id"] is None
            status = client.get("/api/v1/catalog/steam/status").json()
            # API is deliberately disabled; a successful fixture does not change operator consent.
            assert status["status"] == "disabled"
            assert status["discovered_games"] == 1 and status["metadata_pending"] == 1
        print(
            "Synthetic Steam page -> SQL -> real HTTP directory/status passed; no external requests"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
