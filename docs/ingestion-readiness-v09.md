# Steam / IGDB ingestion preflight (v0.9 P1)

The v0.8 ingestion workers are opt-in and require operator-owned credentials. This preflight makes the distinction between configuration, worker/runtime status, and completed data ingestion explicit.

## What it checks

`scripts/check_ingestion_readiness.py` is read-only. It:

- Reads the same validated `Settings` used by the API and workers.
- Reports whether Steam sync and IGDB enrichment are enabled and whether required credential fields are present.
- Reports the configured page/batch size, polling/refresh interval, daily request budget, and retrieval candidate cap.
- Reports whether recommendation execution is configured for embedded or external worker mode.
- Optionally reads the PlayMatch `/health`, Steam status, and IGDB enrichment status endpoints.
- Includes only allow-listed status fields and never prints credential values, arbitrary upstream response bodies, or exception messages.

It does **not** authenticate to Steam or IGDB, start workers, write to the database, trigger a sync, or prove that a provider is reachable. A `configured_not_probed` state means only that the required configuration fields are present and the feature is enabled.

## Configuration-only check

Run from the repository root after installing the locked environment:

```bash
uv run python scripts/check_ingestion_readiness.py
```

This prints a JSON report and exits successfully even when optional ingestion is disabled. That makes it suitable for checking a normal local development environment without credentials.

To require both integrations to be enabled and configured:

```bash
uv run python scripts/check_ingestion_readiness.py --require-steam --require-metadata
```

The strict command exits with code 2 when a requirement is not met. It does not echo environment values.

## Runtime status check

Start the API first, let its Alembic migrations complete, and then run:

```bash
uv run python scripts/check_ingestion_readiness.py \
  --api-base-url http://127.0.0.1:8000
```

The probe reads only:

- `GET /health`
- `GET /api/v1/catalog/steam/status`
- `GET /api/v1/catalog/enrichment/status`

Plain HTTP is accepted only for loopback addresses. Remote origins must use HTTPS. Redirects are not followed. Runtime responses are reduced to an allow-list before they are printed.

To require reachable API endpoints:

```bash
uv run python scripts/check_ingestion_readiness.py \
  --api-base-url http://127.0.0.1:8000 \
  --require-api
```

## Deployment acceptance gates

After the operator configures the server-side Steam Web API key and IGDB client credentials, and explicitly enables both workers, start the optional profiles using the v0.8 operating instructions. Then run the runtime preflight again.

The following strict gates distinguish credentials from actual ingestion evidence:

```bash
# Configuration fields are present and features are enabled.
uv run python scripts/check_ingestion_readiness.py \
  --require-steam --require-metadata

# API status endpoints are reachable, a full Steam sync has completed,
# and at least one automatic IGDB metadata record is available.
uv run python scripts/check_ingestion_readiness.py \
  --api-base-url http://127.0.0.1:8000 \
  --require-api \
  --require-steam-full-sync \
  --require-metadata-success
```

The full-sync gate requires a recorded `last_full_success_at` and a non-zero discovered identity count. The metadata gate requires a recorded successful enrichment and at least one eligible automatic metadata record. These are minimum smoke-acceptance gates, not proof of complete Steam coverage, complete IGDB matching, correct recommendation relevance, or a freshness SLA.

For a larger acceptance report, also inspect the full status responses and compare:

- Steam `discovered_games`, `metadata_pending`, `last_full_success_at`, cursor, cycle and request budget.
- IGDB `jobs_by_state`, `eligible_metadata`, `automatic_metadata`, last success and reserved request budget.
- Source evidence, platform/language uncertainty, quarantined conflicts and retry/backoff states.

Do not run synthetic capacity scripts against the runtime database. PostgreSQL integration tests require a dedicated empty disposable database and the explicit opt-in described in the test documentation.

## Credential handling

Put secrets only in the server-side `.env` or deployment secret manager. Never paste keys into chat, command-line arguments, issue descriptions, PR comments, logs, screenshots, or Git. Rotate a credential if it has been exposed. The preflight reports only booleans indicating whether required values are present.
