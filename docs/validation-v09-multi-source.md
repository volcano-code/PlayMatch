# PlayMatch v0.9 — Steam-first multi-source metadata engine

Date: 2026-11-18.

## Scope

This increment replaces the default IGDB-only enrichment dependency with a provider-neutral metadata interface and a Steam-first implementation. It retains the existing Steam identity directory, enrichment job/lease/quota machinery, normalized Game payload and recommendation worker.

## Delivered

- Added a common asynchronous `MetadataProvider` protocol and a provider factory used by the existing enrichment worker.
- Added Steam Store AppDetails as the default best-effort primary metadata source for already-known Steam AppIDs. This endpoint is undocumented and is not described as an official Valve Web API.
- Added optional RAWG metadata supplementation. A RAWG candidate is attached only after its per-game store-link response contains an HTTPS Steam Store URL with the exact requested AppID. Title similarity is never sufficient for identity.
- Kept IGDB behind an optional legacy adapter selected with `METADATA_PROVIDER=igdb`; Twitch 2FA is no longer a prerequisite for the default Steam Store path.
- Added multiple-source provenance support with Alembic migration 0008. Each source has a distinct provider ID, observation timestamp and content hash.
- Preserved field-level evidence and conservative unknown states. Missing Chinese support, co-op, horror and other facts are not converted into negative claims.
- Preserved curated/operator-imported metadata from automatic overwrite.
- Added bounded request reservations. Steam-only mode does not need a metadata API key; RAWG usage is optional and separately budgeted.
- Updated Compose, environment examples, preflight reporting, README, deployment and provider documentation.

## Configuration

Default no-Twitch path:

```dotenv
METADATA_PROVIDER=steam_rawg
METADATA_ENRICHMENT_ENABLED=true
METADATA_BATCH_SIZE=10
METADATA_DAILY_REQUESTS=600
METADATA_REFRESH_SECONDS=604800
RAWG_API_KEY=
```

Steam Store AppDetails is the primary source; RAWG supplements it only when configured and exact Steam identity is verified. RAWG free-tier/non-commercial and attribution requirements must be reviewed before public or commercial use. Do not assume a free key grants commercial rights.

## CI validation

Final full CI run for commit `06c12e858c0650de4bc18678269eba00840f4c2f`:
[GitHub Actions 44321715823](https://github.com/volcano-code/PlayMatch/actions/runs/44321715823).

- All three CI jobs passed: Python/PostgreSQL, web/browser, and container startup.
- Ruff lint and format passed.
- Full Python suite: **406 passed**.
- Existing PostgreSQL regression suites passed, including the new multi-source migration/provider tests.
- Fixed recommendation evaluation and catalog challenge checks passed.
- Frontend typecheck, format, unit tests and production build passed.
- Playwright browser suite: **25 passed**.
- Compose configuration and default container startup passed.
- Existing independent recommendation worker smoke, notification fixture, Steam identity fixture and metadata-to-recommendation fixture passed.
- Source snapshot workflow passed.

The CI uses deterministic fixtures and synthetic catalog records. It does not call Steam Store, RAWG or IGDB live.

## Known limitations and non-claims

- Steam Store AppDetails is undocumented and can throttle, change or become unavailable. It is best-effort, not a supported official metadata API contract.
- Steam Store language/category statements are provider evidence, not independent installation QA.
- RAWG is optional. Free-tier use is described by RAWG as non-commercial and attribution-bearing; commercial/public monetized use requires a separate license/plan review.
- RAWG metadata is accepted only after exact Steam store-link validation; this may reduce match coverage. No title-only fallback exists.
- Developer, publisher and release-date fields are not yet exposed in the public Game schema in this increment.
- No live credentials, live provider calls, real catalog coverage, provider match rate, freshness SLA, recommendation quality improvement or production P95 is claimed.
- The curated starter recommendation catalog remains 48 games until real provider ingestion is configured and verified.

## Next work

1. Add explicit per-provider request/response and data-quality counters to the read-only enrichment status report.
2. Add a safe one-batch operator command with dry-run/preview and provenance report before production ingestion is expanded.
3. Run a small real Steam Store sample after operator review of the undocumented endpoint and rate behavior.
4. Add a RAWG live probe only after a key is supplied locally and the applicable attribution/license conditions are confirmed.
5. Measure field coverage and rejection reasons before tuning metadata priorities or expanding the catalog.
