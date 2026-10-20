#!/usr/bin/env python3
"""Validate a reviewed normalized IGDB manifest. Dry-run unless --apply is explicit."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))

from playmatch.catalog_collect import unwrap_review
from playmatch.catalog_import import MAX_FILE_BYTES, import_catalog  # noqa: E402
from playmatch.config import Settings  # noqa: E402
from playmatch.database import Database  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--key", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approve-sha256", help="Required to apply a collected review bundle")
    args = parser.parse_args()
    database = None
    try:
        with args.manifest.open("rb") as handle:
            body = handle.read(MAX_FILE_BYTES + 1)
        if len(body) > MAX_FILE_BYTES:
            raise ValueError("Import size limit exceeded")
        manifest, review_hash = unwrap_review(
            json.loads(body), approval=args.approve_sha256, apply=args.apply
        )
        database = Database(Settings().database_url)
        database.migrate()
        report = import_catalog(
            database, manifest, args.key, dry_run=not args.apply, review_hash=review_hash
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, SQLAlchemyError):
        print(
            "Import rejected. Check schema, identities, timestamp and database; no partial batch was committed.",
            file=sys.stderr,
        )
        return 2
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
