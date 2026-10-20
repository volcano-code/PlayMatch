export type Country = "US" | "CN";
export interface Evidence {
  label: string;
  url: string;
  field: string;
  observed_at: string | null;
}
export interface Game {
  id: string;
  steam_app_id: number;
  title: string;
  title_zh: string;
  description: string;
  themes: string[];
  genres: string[];
  platforms: string[];
  chinese_support: "verified" | "unknown" | "unsupported";
  coop: "online" | "local" | "none" | "unknown";
  horror: "yes" | "no" | "unknown";
  image_url: string;
  store_url: string;
  evidence: Evidence[];
  metadata_status: "curated" | "provider";
  estimated_hours: number | null;
}
export interface Offer {
  id: string;
  store: string;
  price_minor: number;
  regular_price_minor: number;
  currency: "USD";
  url: string;
  country: string;
  fetched_at: string;
  source_updated_at: string | null;
  edition: string;
  activation_status: "unknown" | "verified" | "unsupported";
  eligible_for_best: boolean;
  provider: "cheapshark";
}
export interface OfferResponse {
  game_id: string;
  supported: boolean;
  country: string;
  currency: string | null;
  offers: Offer[];
  best_offer_id: string | null;
  fetched_at: string | null;
  status: "ok" | "partial" | "unsupported" | "unavailable";
  notices: string[];
}
export interface Recommendation {
  game: Game;
  reasons: string[];
  tradeoffs: string[];
  score: number;
  evidence_ids: string[];
}
export interface Run {
  id: string;
  status: "queued" | "running" | "completed" | "partial" | "failed";
  query: string;
  intent: Record<string, unknown>;
  results: Recommendation[];
  warnings: string[];
  mode: "rules" | "llm";
  created_at: string;
  completed_at: string | null;
}
export interface User {
  id: string;
  email: string;
}
export interface Capabilities {
  catalog_mode: "curated_starter" | "igdb" | "mixed";
  retrieval_mode: "keyword_baseline" | "bm25" | "hybrid" | "indexed_hybrid";
  embedding_configured: boolean;
  catalog_import: "operator_cli";
  semantic_catalog_limit: number;
  agent_mode: "rules" | "llm";
  pricing_provider: string;
  pricing_currency: string;
  supported_price_countries: string[];
  limitations: string[];
}
export interface WatchItem {
  game: Game;
  target_minor: number | null;
  currency: string;
  country: string;
  monitoring_status: "not_enabled";
  created_at: string;
}
export type FeedbackKind = "like" | "dislike" | "owned" | "exclude";

export interface IndexStatus {
  status: "not_configured" | "missing" | "ready" | "stale" | "model_mismatch" | "corrupt";
  active_index_id: string | null;
  generation: number;
  document_count: number;
  dimensions: number | null;
  catalog_fingerprint: string | null;
  search_method: "exact_cosine";
  max_documents: number;
  provider_probed: false;
}
