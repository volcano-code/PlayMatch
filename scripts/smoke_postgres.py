"""Exercise a disposable PostgreSQL database, including a second application start.

The caller must provide DATABASE_URL pointing at a development/CI database. This
script creates only its own uniquely named account and related test records; it
does not drop, truncate, or reset tables. No provider or model requests are made.
"""

import os
from uuid import uuid4

from fastapi.testclient import TestClient
from playmatch.config import Settings
from playmatch.main import create_app


def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise SystemExit("DATABASE_URL must identify a disposable PostgreSQL database")
    settings = Settings(database_url=url, llm_api_key="", llm_model="", _env_file=None)
    email = f"postgres-{uuid4().hex}@example.test"
    credentials = {"email": email, "password": "PlayMatch-test-only-438!"}

    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["catalog_count"] > 0
        registered = client.post("/api/v1/auth/register", json=credentials)
        assert registered.status_code == 201, registered.status_code
        user_id = registered.json()["id"]
        game = client.get("/api/v1/games").json()["items"][0]
        response = client.post(
            "/api/v1/watchlist",
            json={"game_id": game["id"], "target_minor": 1000, "currency": "USD", "country": "US"},
        )
        assert response.status_code == 200, response.status_code
        assert response.json()["monitoring_status"] == "not_enabled"
        response = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": str(uuid4())},
            json={"query": "探索科幻游戏", "country": "US"},
        )
        assert response.status_code == 202, response.status_code
        run_id = response.json()["id"]
        events = client.get(f"/api/v1/runs/{run_id}/events")
        assert events.status_code == 200
        assert "event: done" in events.text
        completed = client.get(f"/api/v1/runs/{run_id}").json()
        assert completed["status"] in {"completed", "partial"}, completed["status"]
        assert completed["results"]
        assert client.post("/api/v1/auth/logout").status_code == 204

    # Re-run Alembic at application start; records must survive the new engine.
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/auth/login", json=credentials)
        assert response.status_code == 200
        assert response.json()["id"] == user_id
        assert client.get("/api/v1/watchlist").json()["items"][0]["game"]["id"] == game["id"]
        assert client.get(f"/api/v1/runs/{run_id}").json()["results"]
        assert client.delete(f"/api/v1/watchlist/{game['id']}").status_code == 204
        assert client.get("/api/v1/watchlist").json()["items"] == []

    print("PostgreSQL migration, authenticated run, events and restart persistence passed")


if __name__ == "__main__":
    main()
