"""Centralized configuration for the Memoir engine.

One Settings class, one entry point (`get_settings()`), one `.env` file.
Every other module reads config through this — no `os.environ.get(...)`
sprinkled across the codebase.

Source order (Pydantic Settings standard):
1. Constructor kwargs (used only in tests when overriding).
2. Environment variables (prefix `MEMOIR_`).
3. `.env` file in repo root (gitignored).
4. Defaults below (sensible for `docker compose up`).

The defaults match `docker-compose.yml` exactly so a fresh clone +
`docker compose up -d` + `uv run pytest` works without any `.env` at
all. Production / CI overrides go through env vars or a `.env` file.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEMOIR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # Database (M1 substrate, used everywhere)
    # ------------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://memoir:memoir@localhost:5432/memoir",
        description="SQLAlchemy DSN for the production / runtime database.",
    )
    test_database_url: str = Field(
        default="postgresql+psycopg://memoir:memoir@localhost:5432/memoir_test",
        description=(
            "SQLAlchemy DSN for the pytest database. Tests fall back to "
            "skipping the DB-dependent suite if this DSN is unreachable."
        ),
    )

    # ------------------------------------------------------------------
    # LLM extractor (M2 — memoir.extract.llm)
    # ------------------------------------------------------------------
    llm_base_url: str | None = Field(
        default=None,
        description=(
            "OpenAI-compatible endpoint for the LLM extractor. Point at "
            "vLLM (e.g. http://localhost:8000/v1). None disables the LLM "
            "path; RuleExtractor still works."
        ),
    )
    llm_api_key: str = Field(
        default="EMPTY",
        description=(
            "API key for the LLM endpoint. Local vLLM ignores it; OpenAI "
            "or hosted endpoints need a real key. Default 'EMPTY' is the "
            "vLLM convention."
        ),
    )
    llm_model: str = Field(
        default="Qwen/Qwen2.5-7B-Instruct",
        description="Model id the LLM extractor requests from the endpoint.",
    )
    llm_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description=(
            "Sampling temperature for the extractor. 0.0 is the default "
            "because §9 prefers under-extraction over creative guesses."
        ),
    )

    # ------------------------------------------------------------------
    # LLM live-endpoint test gate (test_extraction.py)
    # ------------------------------------------------------------------
    llm_test_base_url: str | None = Field(
        default=None,
        description=(
            "If set, test_llm_extractor_against_live_endpoint runs against "
            "this endpoint. If unset, the test is skipped — CI without a "
            "live model stays green."
        ),
    )
    llm_test_model: str | None = Field(
        default=None,
        description="Override `llm_model` for the live-endpoint test only.",
    )
    llm_test_api_key: str | None = Field(
        default=None,
        description="Override `llm_api_key` for the live-endpoint test only.",
    )

    # ------------------------------------------------------------------
    # Object storage (MinIO) — future audio ingestion (§5 row 1)
    # ------------------------------------------------------------------
    minio_endpoint: str = Field(
        default="localhost:9000",
        description="MinIO endpoint host:port. No scheme — minio client adds it.",
    )
    minio_access_key: str = Field(default="memoir")
    minio_secret_key: str = Field(default="memoir-secret")
    minio_bucket: str = Field(
        default="memoir",
        description="Default bucket for sources.storage_uri references.",
    )

    # ------------------------------------------------------------------
    # Redis — future orchestration (§5 row "Orchestration")
    # ------------------------------------------------------------------
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis DSN for Celery / Prefect, when wired up post-V1.",
    )

    # Convenience helpers ----------------------------------------------
    @property
    def llm_effective_test_base_url(self) -> str | None:
        """The base URL the live test should hit, falling back to the
        main LLM endpoint if the test override isn't set.
        """
        return self.llm_test_base_url or self.llm_base_url

    @property
    def llm_effective_test_model(self) -> str:
        return self.llm_test_model or self.llm_model

    @property
    def llm_effective_test_api_key(self) -> str:
        return self.llm_test_api_key or self.llm_api_key


@lru_cache
def get_settings() -> Settings:
    """Cached accessor — call sites should use this rather than
    instantiating `Settings()` directly so test overrides via
    `get_settings.cache_clear()` work.
    """
    return Settings()
