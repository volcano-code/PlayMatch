from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field
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
