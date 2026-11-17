# PlayMatch v0.9 P1 — ingestion preflight

Date: 2026-11-17.

## Scope

This increment adds a read-only operator preflight for the existing v0.7 Steam identity worker and v0.8 IGDB metadata-enrichment worker. It does not change either worker's scheduling, provider adapters, database schema, public API contracts, recommendation behavior, or default feature flags.

## Delivered

- `playmatch.ingestion_readiness.build_config_report` reports feature enablement, credential-field presence, configured page/batch limits, request budgets, execution mode, and retrieval candidate cap.
- `probe_runtime` optionally reads only the PlayMatch health, Steam status, and metadata-enrichment status endpoints.
- Runtime payloads are allow-listed; arbitrary fields, provider response bodies, tokens, and secret values are discarded.
- Remote runtime probes require HTTPS. Plain HTTP is restricted to loopback. Redirects are not followed and environment proxy settings are ignored.
- Strict acceptance flags can require configured integrations, reachable status endpoints, a completed Steam full sync, and at least one automatic IGDB metadata record.
- The command never contacts Steam or IGDB, starts workers, mutates the database, or initiates ingestion.
- Unit tests cover disabled/missing/complete configuration, secret redaction, status payload allow-listing, HTTP failures, unsafe API origins, and strict runtime evidence gates.
- README and deployment documentation include the preflight procedure.

## Commands

```bash
uv run python scripts/check_ingestion_readiness.py
uv run python scripts/check_ingestion_readiness.py --api-base-url http://127.0.0.1:8000
uv run python scripts/check_ingestion_readiness.py --require-steam --require-metadata
uv run python scripts/check_ingestion_readiness.py --api-base-url http://127.0.0.1:8000 --require-api --require-steam-full-sync --require-metadata-success
```

## Validation status

The new tests and offline CLI smoke are wired into the repository CI. Their result is pending the final workflow run for this branch. No local test run is claimed in this record.

## Explicitly not claimed

- No Steam or IGDB credentials were supplied or contacted.
- No real full/delta sync or metadata matching run was performed.
- No game coverage, matching-rate, freshness, recommendation-quality, latency, or production-SLA improvement is claimed.
- The readiness report distinguishes credential presence from authentication and successful data ingestion.
