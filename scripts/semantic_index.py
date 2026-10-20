#!/usr/bin/env python3
"""Operator commands for explicit vector builds, status and CAS activation/rollback."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))

from playmatch.config import Settings  # noqa: E402
from playmatch.database import Database  # noqa: E402
from playmatch.models import EmbeddingIndex  # noqa: E402
from playmatch.semantic_index import (  # noqa: E402
    IndexUnavailable,
    _report,
    activate_index,
    build_index,
    delete_index,
    index_status,
)
from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("list")
    build = commands.add_parser("build")
    build.add_argument("--allow-network", action="store_true")
    build.add_argument("--resume")
    build.add_argument("--activate", action="store_true")
    build.add_argument("--timeout", type=float, default=300)
    activate = commands.add_parser("activate")
    activate.add_argument("index_id")
    activate.add_argument("--expected-generation", type=int, required=True)
    remove = commands.add_parser("delete")
    remove.add_argument("index_id")
    remove.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if args.command == "delete" and not args.confirm:
        parser.error("Deleting a non-active snapshot requires --confirm")
    if args.command == "build" and not args.allow_network:
        parser.error(
            "Building vectors sends public catalog descriptions to the configured provider; use --allow-network"
        )
    settings, database = Settings(), None
    try:
        database = Database(settings.database_url)
        database.migrate()
        if args.command == "status":
            result = index_status(database, settings)
        elif args.command == "list":
            with database.sessions() as session:
                result = [
                    _report(row)
                    for row in session.scalars(
                        select(EmbeddingIndex).order_by(EmbeddingIndex.created_at.desc()).limit(20)
                    )
                ]
        elif args.command == "delete":
            result = delete_index(database, args.index_id)
        elif args.command == "activate":
            result = activate_index(database, settings, args.index_id, args.expected_generation)
        else:
            result = build_index(
                database,
                settings,
                resume_id=args.resume,
                activate=args.activate,
                timeout_seconds=args.timeout,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (IndexUnavailable, ValueError, SQLAlchemyError):
        print(
            "Index command failed. Existing active index was not replaced by an incomplete build. Use list/status to inspect safe failure codes.",
            file=sys.stderr,
        )
        return 2
    finally:
        if database:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
