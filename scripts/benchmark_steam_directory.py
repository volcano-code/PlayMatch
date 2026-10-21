"""Synthetic scale smoke; never accesses Steam and never represents live coverage.

Uses a fresh temporary SQLite DB, or an explicitly permitted disposable PostgreSQL
DB from STEAM_BENCH_DATABASE_URL. Exercises the same page/transaction/query path.
"""

import argparse
import json
import os
import platform
import tempfile
import time
from pathlib import Path

from playmatch.config import Settings
from playmatch.database import Database
from playmatch.models import SteamDirectoryApp, SteamSyncReceipt, SteamSyncState
from playmatch.providers.steam_catalog import parse_page
from playmatch.steam_sync import SteamSynchronizer, directory_page
from sqlalchemy import delete, func, select, update


def run(url, size):
    db = Database(url)
    db.migrate()
    with db.sessions() as session, session.begin():
        for model in (SteamDirectoryApp, SteamSyncReceipt, SteamSyncState):
            session.execute(delete(model))
        session.add(SteamSyncState(id=1))
    settings = Settings(
        _env_file=None,
        steam_sync_enabled=True,
        steam_web_api_key="SYNTHETIC_NO_NETWORK",
        steam_sync_page_size=5000,
    )
    worker = SteamSynchronizer(db, settings)
    start = time.perf_counter()
    for first in range(1, size + 1, 5000):
        last = min(size, first + 4999)
        with db.sessions() as session, session.begin():
            session.execute(update(SteamSyncState).values(next_due=0))
        claim = worker.claim()
        assert claim and claim.cursor == first - 1
        page = parse_page(
            {
                "response": {
                    "apps": [
                        {
                            "appid": i,
                            "name": f"Synthetic fixture {i:07d}",
                            "last_modified": 100,
                            "price_change_number": 0,
                        }
                        for i in range(first, last + 1)
                    ],
                    "have_more_results": last < size,
                }
            },
            claim.cursor,
            5000,
        )
        worker.apply(claim, page)
    sync_seconds = time.perf_counter() - start
    db.close()
    db = Database(url)
    query_start = time.perf_counter()
    with db.sessions() as session:
        actual = session.scalar(select(func.count()).select_from(SteamDirectoryApp))
        assert actual == size
        result = directory_page(session, after_appid=max(0, size - 24), limit=24)
        assert len(result["items"]) == min(24, size) and result["next_appid"] is None
        assert directory_page(session, q=str(size))["items"][0]["appid"] == size
        assert directory_page(session, q="Synthetic fixture 0000001")["items"][0]["appid"] == 1
        receipt = session.scalar(select(SteamSyncReceipt))
        assert receipt.rows_seen == size
        mode = db.engine.dialect.name
    query_seconds = time.perf_counter() - query_start
    db.close()
    return {
        "dataset": "synthetic_generated_names_not_steam",
        "network_requests": 0,
        "rows": actual,
        "storage": mode,
        "python": platform.python_version(),
        "page_size": 5000,
        "restart_read": True,
        "metadata_created": 0,
        "sync_seconds": round(sync_seconds, 3),
        "three_queries_seconds": round(query_seconds, 3),
        "performance_claim": "single smoke execution, not production P95 or upstream coverage",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 6000 <= args.rows <= 500000:
        parser.error("rows must be between 6000 and 500000")
    with tempfile.TemporaryDirectory(prefix="playmatch-steam-bench-") as directory:
        url = os.getenv("STEAM_BENCH_DATABASE_URL")
        if url and os.getenv("PLAYMATCH_ALLOW_DISPOSABLE_DB") != "yes":
            raise SystemExit("Requires explicit disposable database permission")
        result = run(url or f"sqlite:///{directory}/synthetic.sqlite", args.rows)
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
