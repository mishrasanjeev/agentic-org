"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration for AgenticOrg."""

    model_config = {"env_prefix": "AGENTICORG_", "env_file": ".env", "extra": "ignore"}

    # Environment
    env: str = "development"
    log_level: str = "INFO"
    secret_key: str = Field(min_length=16)

    # Database
    db_url: str = "postgresql+asyncpg://agenticorg:agenticorg_dev@localhost:5432/agenticorg"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Object Storage (GCS / S3-compatible)
    storage_bucket: str = "agenticorg-docs-dev"
    storage_region: str = "asia-south1"
    storage_endpoint: str | None = None  # Set for MinIO/S3-compatible; leave empty for GCS

    # LLM — Gemini 2.5 Flash (free tier) as default; switch to Claude for production
    llm_primary: str = "gemini-2.5-flash"
    llm_fallback: str = "gemini-2.5-flash-preview-05-20"
    llm_temperature: float = 0.2

    # Auth
    auth_provider: str = "grantex"
    jwt_public_key_url: str = ""
    jwt_issuer: str = ""  # Grantex token server issuer URI (AGENTICORG_JWT_ISSUER)
    token_ttl_minutes: int = 60

    # CORS
    cors_allowed_origins: str = ""  # Comma-separated origins; empty = allow all in dev

    # Platform behaviour
    pii_masking: bool = True
    data_region: str = "IN"
    audit_retention_years: int = 7
    max_concurrent_workflows: int = 500
    default_hitl_threshold_inr: int = 500_000
    default_confidence_floor: float = 0.88
    max_agent_retries: int = 3


class ExternalKeys(BaseSettings):
    """External API keys — separate class to avoid prefix collision."""

    model_config = {"env_file": ".env", "extra": "ignore"}

    google_gemini_api_key: str = ""  # Free at aistudio.google.com
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    grantex_client_id: str = ""
    grantex_client_secret: str = ""
    grantex_token_server: str = ""
    langsmith_api_key: str = ""
    langsmith_project: str = "agenticorg-production"
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "agenticorg-core"
    slack_bot_token: str = ""
    slack_hitl_channel: str = "#hitl-approvals"
    sendgrid_api_key: str = ""
    hitl_notification_email: str = ""
    pinelabs_plural_api_key: str = ""
    pinelabs_plural_api_secret: str = ""


settings = Settings()
external_keys = ExternalKeys()
