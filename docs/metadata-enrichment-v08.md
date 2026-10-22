# v0.8 — Automatic metadata and bounded recommendation retrieval

This release connects Steam discovery identities to IGDB metadata and to the existing recommendation workflow. It does not claim that the current environment has acquired the live Steam library. No Steam/IGDB credentials were supplied for this increment; HTTP fixtures remain explicitly synthetic. The 48-game offline catalog and prior evaluation labels are unchanged.

## Pipeline and activation

`catalog-sync -> SteamDirectoryApp -> persistent metadata_jobs -> exact AppID lookup -> guarded GameRecord + SQL postings -> recommendation worker`.

After backing up the runtime database, stop old API/worker processes, update source and retain the database volume. The API runs migration 0007; avoid concurrent schema migrators. The migration backfills existing lexical postings in bounded batches without replacing game JSON or IDs.

Server-only environment (never commit actual secrets):

```dotenv
STEAM_SYNC_ENABLED=true
STEAM_WEB_API_KEY=<server-side Steam key>
IGDB_CLIENT_ID=<server-side Twitch client id>
IGDB_CLIENT_SECRET=<server-side Twitch client secret>
METADATA_ENRICHMENT_ENABLED=true
METADATA_BATCH_SIZE=10
METADATA_DAILY_REQUESTS=1000
METADATA_REFRESH_SECONDS=604800
RETRIEVAL_CANDIDATE_LIMIT=256
```

```sh
docker compose down
docker compose --profile catalog-sync --profile metadata-enrichment up --build -d --wait
docker compose logs -f catalog-sync metadata-enrichment
```

Defaults keep both external ingestion processes disabled. Existing demo/recommendation functionality needs no new key. `python -m playmatch.metadata_worker --once` handles one due batch and respects leases, opt-in, budget and backoff. It does not migrate or reset state. The optional service has no inherited API-port healthcheck; use its status endpoint and process supervision. A shared database heartbeat is not proof that every replica or upstream is healthy.

## Matching, provenance and conservative fields

Official API contract checked 2026-10-22: https://api-docs.igdb.com/ (games, external_games, language_supports, multiplayer_modes; current `external_game_source` / `game_type` references rather than deprecated numeric categories).

The internal query contains only bounded numeric Steam AppIDs and requests explicit external Steam identities. It never falls back to matching a game title. Up to 10 identities per batch; 51 returned records is a rejection sentinel rather than silent truncation. Multiple matches, contradictory uid/URL, missing PC/base-game evidence, invalid dates or missing descriptions cannot create a recommendation record. A batch with malformed external mappings fails closed. Normalized Steam IDs accept the uint32 range.

Summaries remain the provider's original text. `title_zh` is a fallback to the original title, not a claimed translation. A small explicit English taxonomy-to-Chinese vocabulary supplies lexical query tags; it does not generate gameplay facts. Every accepted description/taxonomy record has a source and observation time. Source HTML/URLs are not executed as instructions.

Game-wide language records do not prove a specific platform's language support: only a record listing PC alone and explicitly reporting Simplified Chinese **interface** is used as positive `chinese_support` evidence. This means supplier-supported evidence, not independent verification by Valve or a player. Audio, Traditional Chinese, cross-platform ambiguity and absence remain unknown. Cooperation must be explicitly associated with PC platform 6; LAN and another platform's co-op are not promoted. A Horror theme can establish `yes`; absence never establishes `no`. Missing player counts and purchase eligibility remain unknown. No price or artificial reviews are added.

Existing curated or operator-imported metadata is preserved. Automatically owned metadata can refresh; conflicting identities, older upstream revisions or changed content under the same source timestamp are quarantined rather than overwriting a newer record. Same-timestamp conflicts may therefore need operator investigation. Manual/curated records are not assigned automatic expiry by this feature.

## Durability, limits and freshness

A database singleton serializes bounded enrollment and one in-flight batch. The scanner enrolls 200 identities at a time, resumes by AppID and repeats after reaching the end. New discoveries and source changes are eventually observed; this is not an instantaneous cross-table subscription. Worker polls every 2 seconds. A scan sweep and work backlog contribute to latency.

Network calls run outside transactions. Source hash (AppID/name/source-modified), lease token and lease expiry are checked before committing. Job result, metadata and lexical postings are written atomically. A killed process leaves a reclaimable lease; late results are rejected. Expired ownership is simulated explicitly in the process-kill test rather than claiming a real 90-second wait.

Accepted automatic metadata has a configurable lifetime (default 7 days); expired/blocked automatic records are excluded from recommendation selection. Source changes seen by the scanner invalidate the previous automatic record while replacement is pending. Historical game details remain readable with source dates; this does not certify that all displayed historical detail is fresh.

No-match/rejected records retry after one day. Transient failures use exponential backoff; repeated attempts enter a one-day cooldown. Provider failures are reduced to fixed error codes. The existing IGDB client may retry or refresh tokens internally. A batch reserves **6 request units** as a conservative upper bound, not a measured successful-call count. Reservations survive restart. Default 1,000 units allow at most 166 full reservations/day, approximately 1,660 identities before retries at batch size 10; large-catalog initial fill and refresh require explicitly sized budgets and actual rate/coverage observation. This is not a full-library freshness SLA. Other applications/CLI collectors using the same credentials are outside this worker's shared budget.

## Retrieval and indexing

`game_search_terms` is a persistent inverted term table with a term index. ORM inserts/updates maintain it in the same transaction as metadata; deletions cascade. A monotonic search revision invalidates large-catalog checkpoints. Direct raw SQL writes must maintain postings themselves; arbitrary external database mutation is not supported.

The tokenizer is `postings-v1`: Unicode normalization, English terms and Chinese bigrams; bounded to 512 distinct postings per document. The old migration depends on this frozen version; future tokenizers need a new version/migration, not editing its historical behavior.

Small eligible corpora (up to the candidate cap) keep their existing ranking behavior. Larger corpora use at most 64 query terms to aggregate posting hits, apply supported hard filters and ownership exclusions before the cap, load at most 256 payloads by default, and then use the existing BM25/rule ranker and final hard filters. Remaining hard conditions are still checked after recall. Broad filter-only queries and incomplete lexical overlap can omit good games. Personal preference influence is limited to the recalled set, not all owned/liked games.

Run intent reports `candidate_source`, eligible corpus size, selected count, truncation and index version. Stage snapshots do not copy the full large corpus. Old small-catalog checkpoint tests remain intact. A concurrent metadata revision change before/during selection causes a bounded task retry.

This is **not whole-library ANN/semantic retrieval**. Existing embedding paths keep their original limits; an incompatible/large index degrades explicitly to lexical ranking. Existing index status/build reads are bounded to the legacy limit plus one, not unlimited JSON materialization. Metadata coverage is now aggregated in SQL. Payload caps do not cap SQL aggregation cost; high-frequency terms, concurrency and production P95 still need measurement.

## Read-only status and operation

GET `/api/v1/catalog/enrichment/status` exposes configured/enabled/worker state separately from actual successful batches, job-state counts, fresh eligible/automatic metadata, last success/error and reserved quota. It never triggers enrichment. The frontend labels recent batch success as **not full-catalog completion**, provides refresh/error handling, and never asks visitors for credentials.

`/catalog/steam/status` continues to distinguish discovery and linked metadata; its new enrichment flag is configuration, not actual completion. The separate enrichment panel counts usable metadata and excludes expired/blocked records. The legacy coverage API includes historical source records; its legacy `automatic_sync=false` is not the new worker readiness field.

## Validation commands and limits

```sh
uv run pytest -q
uv run python scripts/check_openapi.py
uv run python scripts/benchmark_metadata_search.py --output /tmp/metadata-scale.json
# Dedicated EMPTY disposable PostgreSQL DB only; never use runtime data.
METADATA_TEST_DATABASE_URL=postgresql+psycopg://... PLAYMATCH_ALLOW_DISPOSABLE_DB=yes uv run pytest services/api/tests/test_metadata_v08.py -q
```

The scale script creates 10,000 synthetic **metadata documents plus postings**, not just names, then reopens the DB and checks rare/broad/no-match candidate queries. It makes zero upstream calls and is not a recall/P95 or live coverage result. Browser cases include a real disabled-state read and a labeled synthetic outage. Container smoke requires an explicit fixture flag and feeds synthetic IGDB metadata into real database/posting writes, the real recommendation worker and authenticated/capability HTTP reads. Never run fixture scripts on user data.

Remaining: real credentials/full and delta coverage proof, IGDB availability for unmatched Steam IDs, wide-scale vector retrieval, full Chinese summary translation, independent human recommendation evaluation, CN pricing, continuous price monitoring, external mail and production multi-host/SLA acceptance. This increment adds an automatic path; it does not guarantee every Steam game has a matching complete IGDB record.
