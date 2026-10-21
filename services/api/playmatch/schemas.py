import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Evidence(BaseModel):
    label: str
    url: str
    field: str
    observed_at: str | None = None


class Game(BaseModel):
    id: str
    steam_app_id: int
    title: str
    title_zh: str
    aliases: list[str] = Field(default_factory=list, max_length=20)
    description: str
    themes: list[str]
    genres: list[str]
    platforms: list[str]
    chinese_support: Literal["verified", "unknown", "unsupported"]
    coop: Literal["online", "local", "none", "unknown"]
    horror: Literal["yes", "no", "unknown"]
    image_url: str
    store_url: str
    evidence: list[Evidence]
    metadata_status: Literal["curated", "provider"]
    estimated_hours: float | None = None

    @field_validator("aliases")
    @classmethod
    def bounded_aliases(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 80 or not value.isprintable() for value in values):
            raise ValueError("Aliases must be 1..80 printable characters")
        return list(dict.fromkeys(value.strip() for value in values))


class CatalogCoverage(BaseModel):
    total_games: int
    source_linked_games: int
    description_observed: int
    chinese_interface_verified: int
    online_coop_verified: int
    local_coop_verified: int
    horror_unknown: int
    with_aliases: int
    automatic_sync: bool
    notes: list[str]


class GameList(BaseModel):
    items: list[Game]
    total: int


class Offer(BaseModel):
    id: str
    store: str
    price_minor: int = Field(ge=0)
    regular_price_minor: int = Field(ge=0)
    currency: Literal["USD"]
    url: str
    country: str
    fetched_at: str
    source_updated_at: str | None = None
    edition: str
    activation_status: Literal["unknown", "verified", "unsupported"]
    eligible_for_best: bool
    provider: Literal["cheapshark"]


class OfferResponse(BaseModel):
    game_id: str
    supported: bool
    country: str
    currency: str | None
    offers: list[Offer]
    best_offer_id: str | None
    fetched_at: str | None
    status: Literal["ok", "partial", "unsupported", "unavailable"]
    notices: list[str]
    alert_evaluation: Literal[
        "not_requested",
        "disabled",
        "above_target",
        "queued",
        "already_triggered",
        "unavailable",
        "unsupported",
        "stale",
        "no_qualifying_offers",
        "save_failed",
    ] = "not_requested"


class Recommendation(BaseModel):
    game: Game
    reasons: list[str]
    tradeoffs: list[str]
    score: float
    evidence_ids: list[str]


class RunInput(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    country: Literal["US", "CN"] = "US"
    budget_minor: int | None = Field(default=None, ge=0, le=1_000_000)
    exclude_owned: bool = True

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("请至少输入两个字符描述想玩的游戏")
        return value


class RunCreated(BaseModel):
    id: str
    status: str
    access_token: str | None


class Run(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "partial", "failed", "cancelled"]
    query: str
    intent: dict
    results: list[Recommendation]
    warnings: list[str]
    mode: Literal["rules", "llm"]
    created_at: str
    completed_at: str | None
    attempt_count: int = 0
    last_checkpoint: str | None = None


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def email_is_valid(cls, value: str) -> str:
        value = value.strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("请输入有效邮箱地址")
        return value


class User(BaseModel):
    id: str
    email: str


class WatchInput(BaseModel):
    game_id: str = Field(min_length=1, max_length=80)
    target_minor: int | None = Field(default=None, ge=0, le=1_000_000)
    currency: Literal["USD", "CNY"] = "USD"
    country: Literal["US", "CN"] = "US"

    @model_validator(mode="after")
    def currency_matches_country(self):
        if (self.country, self.currency) not in {("US", "USD"), ("CN", "CNY")}:
            raise ValueError("地区与目标价格币种不匹配")
        return self


class AlertInput(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    enabled: bool
    expected_version: int = Field(ge=0, le=2_000_000_000)
    consent_version: Literal["observed-usd-inapp-v1"] | None = None

    @model_validator(mode="after")
    def explicit_consent(self):
        if self.enabled and self.consent_version != "observed-usd-inapp-v1":
            raise ValueError("开启前必须明确同意查价触发、仅 USD、版本和激活资格未核验的提醒范围")
        return self


class AlertState(BaseModel):
    state: Literal["off", "armed", "pending", "notified", "failed"]
    version: int
    consent_version: str | None
    mode: Literal["own_query_observation"] = "own_query_observation"
    automatic_price_polling: Literal[False] = False
    external_email: Literal[False] = False


class WatchItem(BaseModel):
    game: Game
    target_minor: int | None
    currency: str
    country: str
    monitoring_status: Literal["not_enabled"] = "not_enabled"
    alert: AlertState
    created_at: str


class FeedbackInput(BaseModel):
    game_id: str = Field(min_length=1, max_length=80)
    kind: Literal["like", "dislike", "owned", "exclude"]


class IndexStatus(BaseModel):
    status: Literal["not_configured", "missing", "ready", "stale", "model_mismatch", "corrupt"]
    active_index_id: str | None
    generation: int
    document_count: int
    dimensions: int | None
    catalog_fingerprint: str | None
    search_method: Literal["exact_cosine"]
    max_documents: int
    provider_probed: Literal[False] = False


class NotificationPayload(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    kind: Literal["observed_target_price"] = "observed_target_price"
    game_id: str = Field(min_length=1, max_length=80)
    game_title: str = Field(min_length=1, max_length=300)
    price_minor: int = Field(ge=0, le=1_000_000)
    target_minor: int = Field(ge=0, le=1_000_000)
    currency: Literal["USD"] = "USD"
    country: Literal["US"] = "US"
    store: str = Field(min_length=1, max_length=120)
    offer_url: str = Field(min_length=1, max_length=8192)
    observed_at: str = Field(max_length=40)
    source_updated_at: str | None = Field(default=None, max_length=40)
    purchase_eligibility: Literal["not_verified"] = "not_verified"

    @field_validator("observed_at", "source_updated_at")
    @classmethod
    def timestamp_with_timezone(cls, value):
        from datetime import datetime

        if value is not None:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Observation timestamp requires timezone")
        return value

    @field_validator("offer_url")
    @classmethod
    def safe_deal_link(cls, value):
        from urllib.parse import parse_qs, urlsplit

        parsed = urlsplit(value)
        query = parse_qs(parsed.query, strict_parsing=True)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "www.cheapshark.com"
            or parsed.path != "/redirect"
            or parsed.fragment
            or set(query) != {"dealID"}
            or len(query["dealID"]) != 1
            or not query["dealID"][0]
            or any(ord(char) < 33 for char in value)
        ):
            raise ValueError("Invalid offer link")
        return value


class NotificationItem(BaseModel):
    id: str
    payload: NotificationPayload
    created_at: str
    read_at: str | None


class NotificationList(BaseModel):
    items: list[NotificationItem]
    total: int
    unread: int


class SteamDirectoryItem(BaseModel):
    appid: int
    name: str
    store_url: str
    source_modified: int | None
    observed_at: float
    metadata_status: Literal["available", "pending"]
    game_id: str | None


class SteamDirectoryPage(BaseModel):
    items: list[SteamDirectoryItem]
    next_appid: int | None
    search_mode: Literal["name_prefix_or_exact_appid"]


class SteamDirectoryStatus(BaseModel):
    status: Literal[
        "disabled",
        "not_configured",
        "worker_not_running",
        "backoff",
        "syncing",
        "never_synced",
        "stale",
        "up_to_date",
    ]
    enabled: bool
    configured: bool
    worker_recent: bool
    source: Literal["steam_istoreservice_games"]
    discovered_games: int
    with_recommendation_metadata: int
    metadata_pending: int
    not_seen_in_last_full: int
    last_success_at: float | None
    last_full_success_at: float | None
    cycle_mode: str
    cycle_in_progress: bool
    pages_committed: int
    rows_seen_in_cycle: int
    cursor: int
    watermark: int
    next_due_at: float
    last_error: str | None
    requests_today: int
    request_budget: int
    poll_interval_seconds: int
    automatic_metadata_enrichment: Literal[False] = False
    automatic_price_polling: Literal[False] = False
    notes: list[str]
