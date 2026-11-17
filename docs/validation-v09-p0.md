# PlayMatch v0.9 P0 baseline-alignment record

Date: 2026-11-17.

## Scope

This increment is documentation and operational-contract alignment only. It does not change application code, database schema, API behavior, provider adapters, retrieval algorithms, or runtime defaults.

## Changes

- Replaced the stale v0.6 README introduction and v0.1 quickstart/architecture claims with the current v0.8 implementation baseline.
- Documented the 48-game curated recommendation catalog separately from the Steam identity directory and automatically enriched metadata.
- Corrected the Compose service description: default services include PostgreSQL, API, web, and the independent recommendation worker; Steam sync and metadata enrichment are opt-in profiles.
- Documented the local SQLite embedded-worker mode and the explicit external-worker mode.
- Clarified durable SQL run semantics, application-level stage snapshots, at-least-once replay boundary, and cancellation/reconnect behavior.
- Documented actual Steam/IGDB environment variables, service profiles, status endpoints, and the fact that configured credentials do not prove successful synchronization.
- Clarified the current US/USD CheapShark observation and user-triggered in-app alert boundary.
- Added a current authoritative v0.8 status section above the historical stage log.
- Marked the original v0.1 implementation contract as historical and directed implementers to the current README, deployment guide, OpenAPI export, and stage-specific contracts.

## Validation

This change only updates Markdown documentation. No application tests were executed in the authoring environment, and no test result is claimed here.

The following are the repository's reproducible checks and are expected to run in GitHub Actions for this branch:

```bash
docker compose config --quiet
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python scripts/check_openapi.py
npm --prefix apps/web ci
npm --prefix apps/web run typecheck
npm --prefix apps/web run format:check
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix tests/e2e ci
npx --prefix tests/e2e playwright install chromium
npm --prefix tests/e2e test
```

PostgreSQL, worker, ingestion-fixture, and container checks remain governed by `.github/workflows/ci.yml`. Live Steam/IGDB synchronization is not part of CI and still requires operator-owned credentials and post-deployment verification.

## Explicitly not claimed

- No source-code or runtime feature change.
- No new Steam/IGDB live synchronization or full-catalog import.
- No independent recommendation-quality result.
- No live price, notification delivery, or public production deployment.
- No new local or remote test execution in this documentation-only increment.
