# v0.9 Multi-source metadata engine

This increment replaces the IGDB-only assumption with a provider boundary while retaining IGDB as an optional legacy adapter.

## Provider policy

- **Steam Store AppDetails is the primary metadata source** for a known Steam AppID. The endpoint used by this adapter is a best-effort, undocumented Store endpoint, not a supported official Steam Web API. It is bounded, cached by the existing metadata refresh policy, and must be treated as subject to change or throttling. Steam's official IStoreService identity directory remains the separate v0.7 source.
- **RAWG is an optional supplement**, not a fallback identity authority. A RAWG result is attached only if its returned Stores entry contains a valid HTTPS Steam Store URL whose AppID exactly equals the Steam directory identity. Search title similarity alone is never sufficient.
- **IGDB remains optional** through `METADATA_PROVIDER=igdb`; Twitch 2FA and IGDB credentials are no longer prerequisites for the default Steam/RAWG path.
- **No fabricated negatives:** missing language, co-op, horror, or platform facts remain unknown. Steam's Simplified Chinese language declaration is recorded as supplier evidence, not independent installation verification.
- **Field provenance:** each source contributes its own `CatalogSource` row and field-level evidence. One normalized game can now retain multiple source records.
- **Curated protection:** automatic providers do not overwrite curated/operator-imported game records.

## Configuration

Default path needs no metadata-provider credential:

```dotenv
METADATA_PROVIDER=steam_rawg
METADATA_ENRICHMENT_ENABLED=true
METADATA_BATCH_SIZE=10
METADATA_DAILY_REQUESTS=600
METADATA_REFRESH_SECONDS=604800
RAWG_API_KEY=
```

Steam Store is the primary source even without a RAWG key. Set `RAWG_API_KEY` only after registering with RAWG and reviewing its current API plan and attribution/usage terms. RAWG's published free tier is described as non-commercial with up to 20,000 monthly requests and attribution links on pages using its data; do not assume it is cleared for a monetized/public commercial product. See https://rawg.io/apidocs and https://rawg.io/tos_api.

To keep the legacy IGDB adapter:

```dotenv
METADATA_PROVIDER=igdb
IGDB_CLIENT_ID=...
IGDB_CLIENT_SECRET=...
```

## Runtime behavior

The metadata worker scans Steam directory identities, requests one bounded Steam Store AppDetails batch, and optionally performs RAWG exact-link lookups. If RAWG is unavailable or no exact Steam mapping is present, the Steam record remains usable and the missing RAWG supplement is not treated as a failed Steam record. The provider reserves its maximum configured request units before making calls.

The normalizer requires:
- response AppID exactly equals requested Steam AppID;
- Store type is `game`;
- Windows platform is explicitly true;
- a non-empty description is available;
- Steam/RAWG source URLs and image hosts pass allow-list validation.

RAWG search result candidates and detail responses are both rechecked for the exact Steam AppID. Ambiguous or absent mapping is ignored, never title-matched.

## Known limitations

- Steam AppDetails is undocumented and may change, throttle, or become unavailable. It is not a replacement for a supported official Valve metadata API contract.
- Store categories and supported-language text are supplier statements; they are not independent QA or game-client inspection.
- RAWG tags/genres may be incomplete and do not establish language or co-op support.
- Developer/publisher/release-date fields are not yet added to the public Game schema in this increment.
- RAWG request volume is higher than Steam-only mode. The worker reserves up to 51 request units per batch when RAWG is enabled (one search plus up to three store-link lookups and one detail request per game, plus the Steam batch); size budgets accordingly. RAWG game-list summaries do not guarantee store links, so the implementation verifies identity through the documented per-game store-links endpoint.
- No live provider credentials or network calls are used by CI. A successful fixture run does not prove current upstream availability or real catalog coverage.
