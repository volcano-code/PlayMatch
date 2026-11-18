#!/usr/bin/env python3
"""Check Steam catalog/metadata-provider configuration and optionally read runtime status.

Examples:
  uv run python scripts/check_ingestion_readiness.py
  uv run python scripts/check_ingestion_readiness.py --api-base-url http://127.0.0.1:8000
  uv run python scripts/check_ingestion_readiness.py --require-steam --require-metadata
  uv run python scripts/check_ingestion_readiness.py --api-base-url http://127.0.0.1:8000 --require-api --require-steam-full-sync --require-metadata-success

This command is read-only. It never prints credentials, starts workers, changes the
database, or makes requests to Steam/IGDB. Runtime probing calls only the local
PlayMatch health and status endpoints.
"""

import argparse
import json
import os
import sys

from playmatch.config import Settings
from playmatch.ingestion_readiness import (
    build_config_report,
    probe_runtime,
    requirements_satisfied,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("PLAYMATCH_API_BASE_URL"),
        help="Optional PlayMatch API origin; probes only /health and read-only status routes",
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="Do not probe a running API, even when PLAYMATCH_API_BASE_URL is set",
    )
    parser.add_argument(
        "--require-steam", action="store_true", help="Require enabled Steam sync and a key"
    )
    parser.add_argument(
        "--require-metadata",
        action="store_true",
        help="Require enabled IGDB enrichment and both credentials",
    )
    parser.add_argument(
        "--require-api", action="store_true", help="Require all three PlayMatch status endpoints"
    )
    parser.add_argument(
        "--require-steam-full-sync",
        action="store_true",
        help="Require runtime evidence of a completed full Steam sync",
    )
    parser.add_argument(
        "--require-metadata-success",
        action="store_true",
        help="Require at least one successful automatic metadata record",
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings()
        report = build_config_report(settings)
        if args.api_base_url and not args.config_only:
            report["runtime"] = probe_runtime(args.api_base_url)
        else:
            report["runtime"] = {
                "scope": "not_probed",
                "reason": "api_base_url_not_supplied_or_config_only",
            }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return (
            0
            if requirements_satisfied(
                report,
                require_steam=args.require_steam,
                require_metadata=args.require_metadata,
                require_api=args.require_api,
                require_steam_full_sync=args.require_steam_full_sync,
                require_metadata_success=args.require_metadata_success,
            )
            else 2
        )
    except (ValueError, OSError) as error:
        # Do not echo arbitrary exception text, which may contain user configuration.
        print(json.dumps({"error": type(error).__name__}), file=sys.stderr)
        return 2
    except Exception as error:
        # Pydantic validation messages may include environment input; keep output redacted.
        print(json.dumps({"error": type(error).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
