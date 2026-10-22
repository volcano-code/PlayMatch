"""Explicitly synthetic IGDB metadata -> real SQL postings -> real worker/API integration."""

import asyncio
import os
import re
import time
from uuid import uuid4

import httpx
from playmatch.config import Settings
from playmatch.database import Database
from playmatch.metadata_enrichment import MetadataEnricher
from playmatch.models import SteamDirectoryApp


class SyntheticIGDB:
    async def catalog_page(self, body):
        ids = [int(n) for n in re.findall(r'"([0-9]+)"', body)]
        return [
            {
                "id": appid - 3000000000,
                "name": "SYNTHETIC CONTAINER METADATA " + str(appid),
                "summary": "Synthetic integration fixture, not a real game. "
                + ("v08unicornmarker" if appid == 4000000001 else "otherfixture"),
                "url": "https://www.igdb.com/games/synthetic-container-fixture",
                "updated_at": 1767225600,
                "platforms": [{"name": "PC (Microsoft Windows)"}],
                "game_type": {"type": "Main Game"},
                "themes": [{"name": "Science fiction"}],
                "genres": [{"name": "Puzzle"}],
                "external_games": [
                    {
                        "uid": str(appid),
                        "url": f"https://store.steampowered.com/app/{appid}/",
                        "external_game_source": {"name": "Steam"},
                    }
                ],
            }
            for appid in ids
        ]


def main():
    if os.getenv("PLAYMATCH_ALLOW_METADATA_FIXTURE") != "yes":
        raise SystemExit("Refusing synthetic metadata writes without disposable-test consent")
    settings = Settings(
        metadata_enrichment_enabled=True, igdb_client_id="SYNTHETIC", igdb_client_secret="SYNTHETIC"
    )
    db = Database(settings.database_url)
    try:
        with db.sessions() as s, s.begin():
            s.add(
                SteamDirectoryApp(
                    appid=4000000001,
                    name="SYNTHETIC METADATA FIXTURE",
                    name_normalized="synthetic metadata fixture",
                    source_modified=100,
                    price_change_number=0,
                    first_seen=100,
                    last_seen=100,
                    full_generation=1,
                )
            )
        assert asyncio.run(MetadataEnricher(db, settings, SyntheticIGDB()).tick())
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
            game = client.get("/api/v1/games/steam-4000000001")
            game.raise_for_status()
            assert game.json()["metadata_status"] == "provider"
            response = client.post(
                "/api/v1/runs",
                json={"query": "v08unicornmarker", "country": "CN"},
                headers={"Idempotency-Key": str(uuid4())},
            )
            response.raise_for_status()
            task = response.json()
            for _ in range(60):
                result = client.get(
                    "/api/v1/runs/" + task["id"], headers={"X-Run-Token": task["access_token"]}
                ).json()
                if result["status"] in {"completed", "partial", "failed"}:
                    break
                time.sleep(0.5)
            assert result["status"] in {"completed", "partial"}, result["status"]
            assert result["results"][0]["game"]["id"] == "steam-4000000001"
            status = client.get("/api/v1/catalog/enrichment/status").json()
            assert status["status"] == "disabled"  # API's opt-in remains unchanged by the fixture.
            assert status["automatic_metadata"] >= 1
        print(
            "Synthetic IGDB fixture -> real metadata/postings -> independent recommendation worker/API passed. No upstream requests."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
