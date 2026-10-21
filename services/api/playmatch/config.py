from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./.state/playmatch.db"
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    cookie_secure: bool = False
    session_days: int = Field(default=7, ge=1, le=30)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    igdb_client_id: str = ""
    igdb_client_secret: str = ""
    llm_timeout_seconds: float = Field(default=20, ge=1, le=60)
    run_workers: int = Field(default=2, ge=1, le=8)
    max_pending_runs: int = Field(default=24, ge=1, le=100)
    run_timeout_seconds: int = Field(default=90, ge=10, le=300)
    run_execution_mode: Literal["embedded", "external"] = "embedded"
    run_lease_seconds: float = Field(default=15, ge=3, le=60, allow_inf_nan=False)
    run_poll_seconds: float = Field(default=0.25, ge=0.05, le=5, allow_inf_nan=False)
    run_max_attempts: int = Field(default=3, ge=1, le=5)

    retrieval_mode: Literal["keyword_baseline", "bm25", "hybrid", "indexed_hybrid"] = "bm25"
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_revision: str = Field(default="", max_length=128)
    embedding_api_key: str = ""
    embedding_timeout_seconds: float = Field(default=8, ge=0.1, le=20, allow_inf_nan=False)
    semantic_min_score: float = Field(default=0.65, ge=0, le=1, allow_inf_nan=False)

    steam_web_api_key: SecretStr = SecretStr("")
    steam_sync_enabled: bool = False
    steam_sync_interval_seconds: int = Field(default=600, ge=300, le=86400)
    steam_full_sync_seconds: int = Field(default=604800, ge=86400, le=2592000)
    steam_sync_page_size: int = Field(default=5000, ge=1, le=5000)
    steam_sync_daily_requests: int = Field(default=1000, ge=1, le=10000)

    @property
    def embedding_enabled(self) -> bool:
        try:
            parsed = urlparse(self.embedding_base_url)
            local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            return bool(
                self.embedding_model
                and parsed.hostname
                and (local or (parsed.scheme == "https" and self.embedding_api_key))
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
        except ValueError:
            return False

    @property
    def llm_enabled(self) -> bool:
        parsed = urlparse(self.llm_base_url)
        safe_base = parsed.scheme == "https" or (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        return bool(
            self.llm_api_key
            and self.llm_model
            and safe_base
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )

    @property
    def origins(self) -> set[str]:
        return {
            item.strip().rstrip("/") for item in self.allowed_origins.split(",") if item.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
