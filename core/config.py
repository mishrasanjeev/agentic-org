"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration for AgenticOrg."""

    model_config = {"env_prefix": "AGENTICORG_", "env_file": ".env", "extra": "ignore"}

    # Environment
    env: str = "development"
    log_level: str = "INFO"
    secret_key: str = Field(default="dev-only-secret-key", min_length=16)

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
    llm_routing: str = "auto"  # auto | tier1 | tier2 | tier3 | disabled
    llm_mode: str = "cloud"  # cloud | local | auto

    # Auth
    auth_provider: str = "grantex"
    jwt_public_key_url: str = ""
    jwt_issuer: str = ""  # Grantex token server issuer URI (AGENTICORG_JWT_ISSUER)
    token_ttl_minutes: int = 60

    # Google OAuth
    google_oauth_client_id: str = ""  # Google Cloud Console OAuth 2.0 Client ID

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

    @model_validator(mode="after")
    def validate_production_secret(self) -> Settings:
        """Prevent accidental use of development fallbacks in production."""
        if self.env.lower() == "production":
            if self.secret_key == "dev-only-secret-key":
                raise ValueError(
                    "AGENTICORG_SECRET_KEY must be explicitly set in production"
                )
            # Refuse to start with the dev DB URL — would silently use localhost
            # with default credentials, causing data loss or auth bypass.
            if "agenticorg_dev@localhost" in self.db_url:
                raise ValueError(
                    "AGENTICORG_DB_URL must be explicitly set in production "
                    "(detected dev fallback with localhost credentials)"
                )
            if "localhost" in self.redis_url:
                raise ValueError(
                    "AGENTICORG_REDIS_URL must be explicitly set in production "
                    "(detected localhost fallback)"
                )
        return self


class ExternalKeys(BaseSettings):
    """External API keys — separate class to avoid prefix collision."""

    model_config = {"env_file": ".env", "extra": "ignore"}

    google_gemini_api_key: str = ""  # Free at aistudio.google.com
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    grantex_client_id: str = ""
    grantex_client_secret: str = ""
    grantex_token_server: str = ""
    grantex_api_key: str = ""  # Grantex SDK API key
    grantex_base_url: str = "https://api.grantex.dev"  # Configurable for self-hosted
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
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_contact_email: str = "mailto:push@agenticorg.ai"


settings = Settings()
external_keys = ExternalKeys()
