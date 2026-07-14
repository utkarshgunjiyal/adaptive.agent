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

    # -- Runtime environment ----------------------------------------------------
    # APP_ENV gates behaviour that must never reach production. In particular the
    # deterministic `stub` LLM provider is only permitted in development / test;
    # in production a missing or invalid LLM configuration fails readiness
    # instead of silently degrading to stub answers.
    app_env: str = "development"   # development | test | staging | production

    # -- MongoDB ----------------------------------------------------------------
    mongo_url: str
    db_name: str = "runner_ai"

    # -- Background job queue ----------------------------------------------------
    # backend: inline (default) runs ingestion as in-process asyncio tasks —
    # the preview behaviour. redis pushes the job payload onto a Redis list
    # (JOB_QUEUE_NAME) consumed by the dedicated worker process
    # (`python -m app.worker`) — the Docker Compose production behaviour.
    job_queue_backend: str = "inline"     # inline | redis
    redis_url: str | None = None
    job_queue_name: str = "runner:jobs:document_ingest"
    worker_dequeue_timeout: int = 5

    # -- JWT --------------------------------------------------------------------
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # -- LLM providers (user-owned credentials) ---------------------------------
    # Runner.ai runs entirely on the operator's own LLM credentials. Two
    # providers are supported: OpenRouter (OpenAI-compatible API, the default
    # production provider because it allows switching models via LLM_MODEL
    # without touching application code) and the direct Anthropic API.
    #
    #   provider: auto | openrouter | anthropic | stub
    #     auto  -> openrouter if OPENROUTER_API_KEY is set,
    #              else anthropic if ANTHROPIC_API_KEY is set,
    #              else stub (deterministic, no network — dev/CI only).
    #
    # The model identifier is NEVER hard-coded in application code; it is read
    # from LLM_MODEL and must be valid for the selected provider (for OpenRouter
    # use e.g. "anthropic/claude-3.5-sonnet"; for direct Anthropic use e.g.
    # "claude-sonnet-4-5-20250929").
    llm_provider: str = "auto"
    llm_model: str = "anthropic/claude-3.5-sonnet"
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.3
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2

    # OpenRouter (OpenAI-compatible). Credentials stay server-side; never
    # exposed to the frontend and never logged.
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_http_referer: str | None = None      # optional application URL
    openrouter_app_name: str = "Runner.ai"

    # Direct Anthropic API.
    anthropic_api_key: str | None = None

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
