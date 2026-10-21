"""Disposable-container smoke: synthetic quote -> real outbox/worker/authenticated inbox.

Never calls external price/email services. Explicit opt-in required; only this
script's UUID account and related test rows are created and cleaned up.
"""

import os
import time
from uuid import uuid4

import httpx
from playmatch.config import Settings
from playmatch.database import Database
from playmatch.models import (
    NotificationRecord,
    PriceAlertEvent,
    SessionRecord,
    UserRecord,
    WatchRecord,
)
from playmatch.notifications import CONSENT_VERSION, lock_alerts, record_observation
from playmatch.run_queue import database_now, now_iso
from sqlalchemy import delete


def main():
    if os.getenv("PLAYMATCH_ALLOW_NOTIFICATION_FIXTURE") != "yes":
        raise SystemExit("Explicit disposable notification-fixture opt-in is required")
    database = Database(Settings().database_url)
    user_id = None
    try:
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=5) as client:
            assert (
                client.get("/api/v1/capabilities").json()["execution_mode"]
                == "durable_sql_external"
            )
            created = client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"smoke-{uuid4().hex}@example.test",
                    "password": "Synthetic-smoke-only-785!",
                },
            )
            created.raise_for_status()
            user_id = created.json()["id"]
            game = client.get("/api/v1/games?limit=1").json()["items"][0]
            saved = client.post(
                "/api/v1/watchlist", json={"game_id": game["id"], "target_minor": 1000}
            )
            saved.raise_for_status()
            armed = client.put(
                f"/api/v1/watchlist/{game['id']}/alert",
                json={
                    "enabled": True,
                    "expected_version": saved.json()["alert"]["version"],
                    "consent_version": CONSENT_VERSION,
                },
            )
            armed.raise_for_status()
            with database.sessions() as session, session.begin():
                started = database_now(session)
                observed = now_iso()
                result = {
                    "supported": True,
                    "country": "US",
                    "currency": "USD",
                    "status": "partial",
                    "offers": [
                        {
                            "provider": "cheapshark",
                            "country": "US",
                            "currency": "USD",
                            "price_minor": 799,
                            "store": "Synthetic container fixture",
                            "fetched_at": observed,
                            "activation_status": "unknown",
                            "source_updated_at": None,
                            "url": "https://www.cheapshark.com/redirect?dealID=synthetic-container-fixture",
                        }
                    ],
                }
                assert record_observation(session, user_id, game, result, started) == "queued"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                inbox = client.get("/api/v1/notifications").json()
                if inbox["total"]:
                    assert inbox["total"] == inbox["unread"] == 1
                    item = inbox["items"][0]
                    assert item["payload"]["store"] == "Synthetic container fixture"
                    assert item["payload"]["purchase_eligibility"] == "not_verified"
                    read = client.post(f"/api/v1/notifications/{item['id']}/read")
                    read.raise_for_status()
                    assert read.json()["read_at"]
                    print(
                        "Synthetic quote -> SQL outbox -> independent worker -> authenticated inbox passed; no external price/email call"
                    )
                    return
                time.sleep(0.2)
            raise SystemExit("Notification worker did not deliver the synthetic test event")
    finally:
        if user_id:
            with database.sessions() as session, session.begin():
                lock_alerts(session)
                for model in (NotificationRecord, PriceAlertEvent, WatchRecord, SessionRecord):
                    session.execute(delete(model).where(model.user_id == user_id))
                session.execute(delete(UserRecord).where(UserRecord.id == user_id))
        database.close()


if __name__ == "__main__":
    main()
