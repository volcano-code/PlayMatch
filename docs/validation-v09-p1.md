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
- CI runs the configuration-only CLI without credentials or external network access.

## Commands

```bash
uv run python scripts/check_ingestion_readiness.py
uv run python scripts/check_ingestion_readiness.py --api-base-url http://127.0.0.1:8000
uv run python scripts/check_ingestion_readiness.py --require-steam --require-metadata
uv run python scripts/check_ingestion_readiness.py --api-base-url http://127.0.0.1:8000 --require-api --require-steam-full-sync --require-metadata-success
```

## Validation

Final GitHub Actions run: [44105062763](https://github.com/volcano-code/PlayMatch/actions/runs/44105062763), commit `0b500ced8fee1c8a9e6ae36ae129b63c66d9baac`.

- Ruff lint: passed.
- Ruff format check: passed.
- Full Python suite: **392 passed**.
- Configuration-only preflight CLI and JSON schema smoke: passed.
- Existing recommendation evaluations and fixed catalog challenge: passed.
- PostgreSQL catalog, index, durable-run, notification, Steam, and metadata suites: passed.
- Synthetic Steam 100k identity and metadata 10k posting capacity smoke: passed; these are synthetic capacity checks, not live catalog coverage.
- OpenAPI consistency and PostgreSQL migration/account persistence smoke: passed.
- Frontend typecheck, formatting, unit tests, and production build: passed.
- Playwright browser suite: **25 passed**.
- Docker Compose configuration, container startup, same-origin API, independent worker, notification fixture, synthetic Steam and metadata HTTP integration: passed.

The first CI attempt identified a missing `math` import and Ruff formatting differences in the new files. These were corrected; the final run above passed all three jobs. The failed attempt is retained in GitHub Actions history.

## Explicitly not claimed

- No Steam or IGDB credentials were supplied or contacted.
- No real full/delta sync or metadata matching run was performed.
- No game coverage, matching-rate, freshness, recommendation-quality, latency, or production-SLA improvement is claimed.
- The readiness report distinguishes credential presence from authentication and successful data ingestion.
