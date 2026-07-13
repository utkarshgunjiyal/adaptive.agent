"""Application configuration loaded from environment via pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- MongoDB ----------------------------------------------------------------
    mongo_url: str
    db_name: str = "runner_ai"

    # -- JWT --------------------------------------------------------------------
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # -- Emergent LLM -----------------------------------------------------------
    emergent_llm_key: str
    llm_model: str = "gpt-5.2"
    llm_provider: str = "openai"

    # -- External tools ---------------------------------------------------------
    tavily_api_key: str | None = None

    # -- Ingestion --------------------------------------------------------------
    max_upload_bytes: int = 25 * 1024 * 1024
    max_pages: int = 200
    chunk_size: int = 1200
    chunk_overlap: int = 180
    storage_dir: str = "/app/backend/_storage"

    # -- Rate limits (per user, per minute) ------------------------------------
    rate_limit_auth_per_minute: int = 20
    rate_limit_agent_per_minute: int = 30

    # -- CORS -------------------------------------------------------------------
    cors_origins: str = "*"

    def cors_origin_list(self) -> List[str]:
        raw = (self.cors_origins or "").strip()
        if not raw:
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _stringify_cors(cls, value):
        if isinstance(value, list):
            return ",".join(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
