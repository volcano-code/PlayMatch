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
    status: Literal["queued", "running", "completed", "partial", "failed"]
    query: str
    intent: dict
    results: list[Recommendation]
    warnings: list[str]
    mode: Literal["rules", "llm"]
    created_at: str
    completed_at: str | None


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


class WatchItem(BaseModel):
    game: Game
    target_minor: int | None
    currency: str
    country: str
    monitoring_status: Literal["not_enabled"] = "not_enabled"
    created_at: str


class FeedbackInput(BaseModel):
    game_id: str = Field(min_length=1, max_length=80)
    kind: Literal["like", "dislike", "owned", "exclude"]
