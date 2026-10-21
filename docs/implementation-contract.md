# PlayMatch implementation contract v0.1

This is the first working release of the approved eight-week plan, not a claim that all milestones are complete.
Repository: https://github.com/volcano-code/PlayMatch
Implemented v0.1 stack: Next.js/TypeScript web, FastAPI/Pydantic/SQLAlchemy API, LangGraph workflow, PostgreSQL container configuration with SQLite local quickstart. Redis/Celery distributed execution is a later milestone; no inactive worker is presented as production functionality.

## Ownership
- Backend agent: services/api/**, services/worker/**, root pyproject.toml and uv.lock, packages/contracts/openapi.json (exported).
- Frontend agent: apps/web/** only.
- Data agent: data/**, services/api/playmatch/providers/**, services/api/playmatch/catalog_seed.py, tests/provider/**, scripts/probe_providers.py, docs/data-sources.md.
- Root: infra, Dockerfiles/Compose, GitHub Actions, end-to-end tests, root docs, integration corrections.
No agent should commit or push; root handles GitHub writes.
Do not modify another ownership area without sending a message.

## Public API
Base /api/v1. Errors {"detail": string | structured validation errors}. Monetary amounts are integer minor units; currency always explicit.
- GET /health => {"status":"ok","version":"0.1.0","catalog_count":N}
- GET /api/v1/capabilities => object with catalog_mode ("curated_starter" or "igdb"), agent_mode ("rules" or "llm"), pricing_provider ("cheapshark"), pricing_currency ("USD"), supported_price_countries:["US"], limitations:string[].
- GET /api/v1/games?q=&theme=&limit=24&offset=0 => {"items":Game[],"total":int}
- GET /api/v1/games/{id} => Game
- GET /api/v1/games/{id}/offers?country=US => OfferResponse (CN returns supported:false with a truthful message, not converted USD)
- POST /api/v1/runs body {"query":str,"country":"US"|"CN","budget_minor":int|null,"exclude_owned":bool=true}; optional auth, Idempotency-Key required (UUID-like string).
  response 202 {"id":str,"status":str,"access_token":str|null}
  Anonymous run access uses server-issued opaque token via X-Run-Token header (also for streaming via fetch). No auth token in URL.
- GET /api/v1/runs/{id} => Run, requires session or run capability
- GET /api/v1/runs/{id}/events => SSE event name: progress, result, error, done; each data JSON with sequence id; Last-Event-ID honored.
- POST /api/v1/auth/register {"email","password"} => User; sets HttpOnly session cookie
- POST /api/v1/auth/login {"email","password"} => User; sets cookie
- POST /api/v1/auth/logout => 204
- GET /api/v1/auth/me => User or 401
- GET /api/v1/watchlist => {"items":WatchItem[]}
- POST /api/v1/watchlist {"game_id":str,"target_minor":int|null,"currency":"USD"|"CNY","country":"US"|"CN"} => WatchItem, auth required
- DELETE /api/v1/watchlist/{game_id} => 204, auth required
- POST /api/v1/feedback {"game_id":str,"kind":"like"|"dislike"|"owned"|"exclude"} => 201, auth required; saved preferences affect future runs.
- GET /api/v1/providers/health => status and limitations, no secrets.
No external email delivery in v0.1. Watchlist UI must explicitly say "saved target, notification monitoring not enabled" until permitted integration exists.

## Shapes
Game {
 id:string, steam_app_id:number, title:string, title_zh:string,
 description:string, themes:string[], genres:string[], platforms:string[],
 chinese_support:"verified"|"unknown"|"unsupported", coop:"online"|"local"|"none"|"unknown",
 horror:"yes"|"no"|"unknown", image_url:string, store_url:string,
 evidence: {label:string,url:string,field:string,observed_at:string|null}[],
 metadata_status:"curated"|"provider", estimated_hours:number|null
}
Offer {
 id:string, store:string, price_minor:int, regular_price_minor:int, currency:"USD",
 url:string, country:string, fetched_at:string, source_updated_at:string|null,
 edition:string, activation_status:"unknown"|"verified"|"unsupported",
 eligible_for_best:bool, provider:"cheapshark"
}
OfferResponse {
 game_id:string, supported:bool, country:string, currency:string|null,
 offers:Offer[], best_offer_id:string|null, fetched_at:string|null,
 status:"ok"|"partial"|"unsupported"|"unavailable", notices:string[]
}
Recommendation { game:Game, reasons:string[], tradeoffs:string[], score:number, evidence_ids:string[] }
Run {
 id:string,status:"queued"|"running"|"completed"|"partial"|"failed",
 query:string,intent:object,results:Recommendation[],warnings:string[],
 mode:"rules"|"llm",created_at:string,completed_at:string|null
}
User { id:string,email:string }
WatchItem {game:Game,target_minor:int|null,currency:string,country:string,monitoring_status:"not_enabled",created_at:string}

## Provider contract
providers/cheapshark.py defines CheapSharkClient(httpx async client optional), async get_offers(steam_app_id:int,country:str="US")->dict in OfferResponse-compatible format excluding game_id (caller adds).
No API key needed. Only user-triggered calls. Respect 429 Retry-After; bounded timeout; no auto access to redirect links. No bulk price polling.
Unknown activation/edition verification must NOT silently produce a guaranteed eligible best price. Can show observed provider prices with uncertainty.
providers/igdb.py defines optional IGDBClient client-credentials integration with rate limiting, parsing and explicit not_configured outcome; no actual token in repo or logs.
catalog_seed.py defines load_catalog() -> list[dict] matching Game shape; source file data/catalog.json; no invented current prices.
Providers may return error states or raise typed exceptions, coordinate actual names with backend agent.

## UI requirements
Polished Chinese product interface, dark navy/purple plus mint accent, actual screenshot/card images via Steam CDN or clean fallback.
Search and theme chips, editable region/budget, recommendation cards, real price drawer, 3-game comparison, auth, watchlist, owned/like feedback, evidence, error/empty/partial states.
Display data mode and price coverage plainly; no fake live values, fabricated user statistics or claims of active notifications.
Same-origin browser API proxy via Next route handler; server API_INTERNAL_URL default http://127.0.0.1:8000.
Use TanStack Query; no remote font dependency at build; responsive and keyboard accessible.

## Verification
pytest for money/region/DLC/unknown constraints/timeout/auth ownership/idempotency/SSE.
Playwright real frontend+API end-to-end smoke. Contract fixtures in CI, optional small live provider probe separately.
Provider integration failures must not invent data or block core recommendation.
No telemetry token/credential capture; redact cookies and secrets.


## v0.2 recovery extension

Capabilities adds `retrieval_mode:keyword_baseline|bm25|hybrid` (configured),
`embedding_configured:bool`, `catalog_import:operator_cli`, `semantic_catalog_limit:128`.
`catalog_mode` additionally supports `mixed` when provider entries exist. Run intent
records actual `retrieval:keyword_baseline|bm25|hybrid_rrf` and the retrieval text
fingerprint. Existing Game/Run/Recommendation public shapes remain unchanged.
All hard-condition and pricing constraints still apply after retrieval. Downgraded
hybrid requests are partial, not successful semantic runs. Import is CLI-only,
transactional, preview by default, with immutable identities and curated preservation.
See [implementation and boundaries](retrieval-import-v02.md).


## v0.3 extension

GET `/api/v1/retrieval/index` returns typed IndexStatus (no secrets, no probe): status,
active_index_id, generation, document_count, dimensions, catalog_fingerprint,
search_method=exact_cosine, max_documents=5000, provider_probed=false.
Configured retrieval_mode additionally supports `indexed_hybrid`; actual intent may
be `indexed_hybrid_rrf`, with index_id/index_generation/index_dimensions. Downgraded
indexed requests are partial. Public Game/Recommendation shapes are unchanged.
Capabilities includes catalog_collection=review_bundle_cli and mode-dependent semantic bound.
The new index operations are operator CLI only; no unauthenticated write endpoints.
See docs/catalog-index-v03.md for review approval, durable batches, leases and CAS semantics.

## v0.4 durable tasks

`Run.status` adds `cancelled`; `Run` adds `attempt_count:int` and `last_checkpoint:string|null`.
POST `/api/v1/runs/{id}/cancel` returns Run and requires the same ownership proof as reads
plus existing CSRF controls. Terminal cancellation and completion are idempotent, first
terminal commit wins. POST `/runs` admits capacity/idempotency and task/event together.
Capabilities `execution_mode` is `durable_sql_embedded|durable_sql_external`, with
`run_max_attempts`. Queued/running tasks are not failed merely because API restarted.

Migration 0004 adds leases, deadline and shared admission lock. The database is the
queue truth. Stage snapshots are application-level, not LangGraph native checkpoints.
Only safe committed stages resume; budget is refreshed. Interrupted read-only external
calls may repeat. See docs/durable-runs-v04.md for process, retry and privacy boundaries.
