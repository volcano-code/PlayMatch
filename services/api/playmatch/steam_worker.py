"""Independent catalog scheduler: python -m playmatch.steam_worker [--once|--health]."""

import argparse
import json
import signal
import threading

from sqlalchemy.exc import SQLAlchemyError

from playmatch.config import Settings
from playmatch.database import Database
from playmatch.steam_sync import SteamSynchronizer, sync_status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once", action="store_true", help="Process at most one due page; never bypass limits"
    )
    mode.add_argument(
        "--health",
        action="store_true",
        help="Check recent scheduler heartbeat, not upstream health",
    )
    args = parser.parse_args()
    settings = Settings()
    database = Database(settings.database_url)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        # API/explicit migration must run first. Never race startup migrations across replicas.
        if args.health:
            with database.sessions() as session:
                status = sync_status(session, settings)
            return (
                0 if status["enabled"] and status["configured"] and status["worker_recent"] else 1
            )
        if (
            not settings.steam_sync_enabled
            or not settings.steam_web_api_key.get_secret_value().strip()
        ):
            print(json.dumps({"status": "disabled_or_not_configured"}))
            return 2
        sync = SteamSynchronizer(database, settings)
        while not stop.is_set():
            try:
                result = sync.tick()
            except SQLAlchemyError:
                # Do not log DB URL or exception parameters. Uncommitted page is safely replayed.
                result = "database_unavailable"
            if result != "idle":
                print(json.dumps({"steam_sync": result}), flush=True)
            if args.once:
                return 0 if result in ("idle", "page_committed", "cycle_completed") else 2
            stop.wait(5 if result == "database_unavailable" else 1)
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
