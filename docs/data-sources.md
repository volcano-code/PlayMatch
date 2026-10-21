# Data sources and verification boundaries

This document describes implemented v0.1 behavior, not future roadmap features.

## Starter catalog

`data/catalog.json` contains 24 manually curated PC games, each with a Steam AppID,
store URL, an original short Chinese description and editorial theme labels. It
contains **no prices, fabricated ratings, player counts or estimated play times**.
The catalog is deliberately small and is not a complete market index.

Steam product pages were opened during creation. Ten language tables explicitly
listed a Simplified Chinese interface; nine feature tables explicitly listed
Online Co-op. Each positive claim has its own `evidence.field` and `observed_at`.
Chinese interface support is not a promise of Chinese voice acting. Online Co-op
does not establish player count, cross-play, subscription or external-account
requirements. Other fields remain `unknown`; no horror-free claim is inferred
from a missing store tag. All `estimated_hours` are null.

The validated Chinese-interface entries are Stardew Valley, Hades, Portal 2,
It Takes Two, Overcooked! 2, Deep Rock Galactic, Valheim, Terraria, Factorio and
Satisfactory. The validated online-co-op entries are the same list except Hades.
Descriptions are editorial summaries, not copied reviews. Cover URLs refer to
Steam's public image CDN; the web app must provide a fallback for unavailable images.

The default source-tree loader locates `data/catalog.json`. Containers or wheel
installs must include that file and can set `PLAYMATCH_CATALOG_PATH` to its path.
Using an IGDB credential does not change `catalog_mode` to `igdb`; that requires an
actual import and verification pipeline, which v0.1 does not claim to contain.

## CheapShark: implemented user-triggered offers

Official references:

- [API documentation](https://apidocs.cheapshark.com/)
- [Provider background and update policy](https://www.cheapshark.com/about)

The adapter requests a maximum of 60 quotes for one Steam AppID when the user asks
for prices. It may also load the small store-name directory on demand, cached in
process for up to 24 hours. It does not harvest a full game-price catalog, poll
watchlists, follow purchase redirects or place orders.

CheapShark documents USD pricing and generally hourly updates, with possible
variation. PlayMatch enables only its US / USD comparison surface. This is not a
certification that every listed key activates in the United States: the provider
does not supply enough verified activation/edition information for that claim.
`country=CN` therefore returns `unsupported` without querying or converting USD.

Prices use exact integer cents via Decimal. Invalid money, mismatched AppIDs,
obvious DLC/soundtrack/upgrade products and duplicate deals are filtered. Surviving
quotes still carry `activation_status=unknown`, `eligible_for_best=false`, and no
`best_offer_id`. They are displayed as observed prices, not guaranteed best buys.
`edition` retains the provider title; it is not a verified edition equivalence.
`source_updated_at` comes from CheapShark's `lastChange`, a price-change timestamp,
and must not be confused with PlayMatch's `fetched_at` or the provider's last poll.

The adapter keeps the required CheapShark redirect links intact. It honors a short
`Retry-After` within a bounded wait budget; if the server asks for a longer wait it
stops instead of retrying too early. A timeout, malformed response or unavailable
provider returns an honest failure state and never a generated price. Client-owned
connections must be closed with `async with CheapSharkClient()` or `aclose()`.

### Alerts are a later, explicit integration

CheapShark does document hosted email alerts (`set`, `delete`, `manage`, `get`).
They can avoid a self-hosted polling loop. They are **not connected in v0.1**:
watchlist targets are saved locally and monitoring is not enabled. A later release
must obtain explicit consent to pass a user's own verified email to the provider,
implement subscription idempotency/cancellation and distinguish accepted requests
from verified email delivery. A `true` API response alone does not prove delivery.
The documented alert parameters do not provide country or shop filters.

## IGDB: optional configured adapter

Official reference: [IGDB API docs](https://api-docs.igdb.com/).

Set `IGDB_CLIENT_ID` and `IGDB_CLIENT_SECRET` on the API server. Obtain them from
your own confidential Twitch application; do not put credentials in browser code,
repository files, URL query strings or application logs. The adapter sends the
client-credentials exchange as a form body and caches the token only in memory.

`IGDBClient.search_games()` supports bounded search, escaped query text, token
refresh, a per-client request spacing of 260ms and an eight-request semaphore.
Reuse one client in a process. Multiple API replicas need a shared rate limit
before using this adapter at scale; per-process limits alone are not a global
application limit. Without both settings the outcome is `not_configured` and
there is no network call. Configured status is not a successful integration probe.

This adapter returns parsed provider records but does not automatically insert
them into the reviewed catalog. Missing Steam mappings and metadata stay absent.
Business use must follow IGDB's commercial partnership/attribution requirements;
the official docs describe free access, with a commercial partnership process.

## Other sources intentionally not assumed available

- [IsThereAnyDeal API terms](https://docs.isthereanydeal.com/) restrict competitive
  applications and require preserving provided links/data. Its production use for
  this recommendation-plus-comparison service needs a permission decision. The
  existence of `country=CN` parameters does not prove this project has permission
  or complete Chinese-region coverage. No ITAD data or API key is used here.
- [Steam's official IStoreService](https://partner.steamgames.com/doc/webapi/IStoreService)
  documents the catalog and change indicators. It is not assumed to supply a
  guaranteed general-purpose price API. No undocumented Store API is a runtime
  dependency of this release.
- [Steam Store Widget](https://partner.steamgames.com/doc/marketing/widget) can
  display a store purchase option. It is not a backend comparison feed.

## Reproducible validation

Run `uv run --extra dev pytest tests/provider -q` for deterministic HTTP fixtures.
They cover money precision, unsupported countries, identity/DLC filtering,
unknown activation, timeouts, bounded Retry-After behavior, safe links, credential
handling and absent configuration. These are offline contract tests, not proof
that the provider is reachable from a deployment region.

`uv run python scripts/probe_providers.py` makes no network requests. Run
`uv run python scripts/probe_providers.py --live --output data/provider-probe.json`
for a small explicit read-only probe of two games. A maximum of three AppIDs is
accepted. The report stores status/counts, not a replicated price dataset. The
probe never calls alert or purchase endpoints. Optional `--igdb-search` only runs
with `--live`; absent credentials remain `not_configured`.

The checked-in `data/provider-probe.json` is an observation snapshot at its
`checked_at` timestamp, not a fixed current-price fixture. The initial live check
received two Stardew Valley quotes and one Portal 2 quote. All remained partial,
with unknown activation and no guaranteed eligible best offer. No monetary values
are embedded in that diagnostic report; later live results may differ.

Live-probe failures remain failures in the report. Network reachability in one
environment does not establish production availability, supplier SLAs or region
activation rights. Do not manufacture successful quotes for demonstrations.


## v0.6 public-source addition (2026-10-21)

24 additional distinct Steam product pages were read directly via web retrieval,
without invoking IGDB credentials or implementing a store scraper. Original
Chinese summaries and editorial aliases are source-linked, not independently
human-reviewed. Language/features evidence is separate from community tags and
not a guarantee about future availability. No prices or reviews are copied into
the catalog. See data/catalog-v06-provenance.json and docs/catalog-evaluation-v06.md.
