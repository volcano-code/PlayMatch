"""Independent SQL queue worker with one isolated process per claimed task.

Run multiple workers against PostgreSQL. Each worker supervises one task at a time;
no broker and no credential-bearing command arguments are required.
"""

import argparse
import logging
import multiprocessing
import signal
import threading
import time
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from playmatch.config import Settings
from playmatch.database import Database
from playmatch.notifications import NotificationOutbox
from playmatch.run_queue import Claim, RunQueue
from playmatch.runs import execute_claim

logger = logging.getLogger(__name__)
HEALTH_PATH = Path("/tmp/playmatch-worker-health")


def child(settings_dict: dict, claim: Claim):
    settings = Settings(_env_file=None, **settings_dict)
    database = Database(settings.database_url)
    try:
        execute_claim(database, settings, claim)
    finally:
        database.close()


def supervise(database, settings, claim, stop: threading.Event):
    queue = RunQueue(database, settings)
    process = multiprocessing.get_context("spawn").Process(
        target=child, args=(settings.model_dump(), claim)
    )
    started = time.monotonic()
    try:
        process.start()
        while process.is_alive():
            process.join(timeout=0.25)
            HEALTH_PATH.touch(mode=0o600, exist_ok=True)
            if (
                stop.is_set()
                or time.monotonic() - started >= claim.remaining
                or not queue.is_owned(claim)
            ):
                break
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        # Only a dead child is safe to requeue immediately. Otherwise let expiry
        # and fencing handle it, never start a replacement from this supervisor.
        if process.pid is not None and not process.is_alive():
            queue.abandon(claim)
        process.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Process at most one currently claimable task"
    )
    parser.add_argument(
        "--health", action="store_true", help="Container-local worker liveness check"
    )
    args = parser.parse_args()
    if args.health:
        try:
            return int(time.time() - HEALTH_PATH.stat().st_mtime > 30)
        except OSError:
            return 1
    settings = Settings()
    database = Database(settings.database_url)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    queue = RunQueue(database, settings)
    try:
        while not stop.is_set():
            try:
                NotificationOutbox(database).process_one()
                claim = queue.claim()
                HEALTH_PATH.touch(mode=0o600, exist_ok=True)
                if claim:
                    supervise(database, settings, claim, stop)
                elif not args.once:
                    stop.wait(settings.run_poll_seconds)
                if args.once:
                    return 0
            except SQLAlchemyError:
                logger.warning("worker_database_unavailable")
                if args.once:
                    return 2
                stop.wait(min(5, settings.run_poll_seconds * 4))
    finally:
        database.close()
        try:
            HEALTH_PATH.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
