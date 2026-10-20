# v0.2 recovery: retrieval and reviewed catalog import

This increment is reconstructed from verified main `871b4a1`. The earlier uncommitted session was not recovered. Its reported test counts, model evaluation and screenshots are not evidence for this increment.

## Retrieval

Default `RETRIEVAL_MODE=bm25` uses frequency-aware BM25 (k1=1.5, b=0.75), NFKC normalization, English terms and Chinese character bigrams. Exact theme matches add a documented lexical bonus. This is lexical retrieval, not a trained semantic model. `keyword_baseline` retains the previous ranker for comparison.

`RETRIEVAL_MODE=hybrid` additionally requires `EMBEDDING_BASE_URL` and `EMBEDDING_MODEL`; remote HTTPS also requires `EMBEDDING_API_KEY`. HTTP is permitted only for explicit loopback services. The endpoint must implement indexed float-vector `/embeddings` responses. The browser never receives the key. Queries and catalog descriptions are sent only after the operator opts in. Secrets and provider response bodies are excluded from returned errors.

The optional branch uses normalized cosine similarity and reciprocal-rank fusion (k=60). `SEMANTIC_MIN_SCORE=0.65` is an uncalibrated operator setting, not a confidence probability. Hard platform/language/co-op/horror/owned filters still run after retrieval; scores do not verify constraints. Requested mode and actual `intent.retrieval` are distinct. Invalid responses, configuration failure or catalog size above 128 explicitly downgrade to BM25 and yield partial runs.

Requests are serialized per configured embedding client, batched by 16 and bounded by timeout, 2 MB decoded response and 2048 dimensions. A 256-document LRU cache keys normalized source text by hash; queries are not retained in this cache. A dimension change rejects and clears the cache. The retrieval fingerprint covers sorted ids and text, not every physical database field. The total-time budget cannot guarantee preemption of blocking system DNS or a stalled OS call. No remote endpoint, quality gain, GPU model, ANN index, cross-encoder or production throughput is claimed by fixture tests.

## Import

The CLI accepts a **reviewed, normalized IGDB adapter manifest**, not arbitrary raw IGDB API responses. There is no public import HTTP endpoint, scheduled synchronization or scraping. Max file size 2 MB, max 200 records per batch, max 5000 stored games. The current seed remains 24 curated games until an operator imports real reviewed data.

```json
{
  "schema_version": 1,
  "source": "igdb",
  "observed_at": "2026-10-19T00:00:00Z",
  "items": [{
    "provider_id": "123",
    "steam_app_id": 100001,
    "title": "Synthetic example - not a real game",
    "description": "Replace with reviewed adapter metadata and genuine provenance.",
    "platforms": ["PC (Microsoft Windows)"],
    "themes": [],
    "genres": [],
    "image_url": "",
    "source_url": "https://www.igdb.com/games/synthetic-example"
  }]
}
```

The example is schema documentation, not a production catalog entry. Never import fabricated provenance as real metadata.

```sh
# Default preview: transaction is rolled back, including receipt and revision.
uv run python scripts/import_catalog.py reviewed.json --key import-001
# Explicit mutation; uses backend DATABASE_URL.
uv run python scripts/import_catalog.py reviewed.json --key import-001 --apply
```

One transaction holds a singleton catalog lock, checks identity and observation timestamps, writes records and a durable idempotency receipt, then advances the revision. Both SQLite and PostgreSQL use this same writer protocol; startup seeding takes the same lock. A later conflict rolls back earlier inserts. Identical records collapse; same key with another canonical payload is rejected. Provider identities cannot be reassigned. Existing curated records and their public ids remain unchanged, preserving watchlist references. Provider-only updates require monotonic observation times, compared as timezone-aware instants.

Imported language, co-op and horror fields remain `unknown`, duration remains null, and the title is not presented as a newly verified Chinese translation. Importing metadata does not verify prices, activation, language or recommendation quality. URL host allowlisting is input hygiene, not authentication of provenance; operator review remains necessary.

## Validation

```sh
uv run pytest -q
uv run python scripts/evaluate_recommendations.py --mode keyword_baseline --check
uv run python scripts/evaluate_recommendations.py --mode bm25 --check --output /tmp/bm25-results.json
# Dedicated disposable PostgreSQL database only: fixture clears catalog tables.
CATALOG_TEST_DATABASE_URL=postgresql+psycopg://... uv run pytest services/api/tests/test_catalog_v02.py -q
```

The existing frozen development labels are not changed. Their positives are non-exhaustive; Hit@k is not precision or satisfaction, and they do not support nDCG claims. Embedding transport tests use synthetic vectors only. Final counts and CI state belong in the PR validation record, not speculative acceptance claims.
