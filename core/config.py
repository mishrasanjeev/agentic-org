"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

STRICT_ENVS = frozenset({"production", "prod", "staging", "stage", "preview"})
RELAXED_ENVS = frozenset({"local", "dev", "development", "test", "ci"})


def normalize_env(env: str | None) -> str:
    """Return the canonical lowercase runtime environment label."""
    return (env or "").strip().lower()


def is_relaxed_env(env: str | None) -> bool:
    """Return True for explicitly local/test runtimes only."""
    return normalize_env(env) in RELAXED_ENVS


def is_strict_runtime_env(env: str | None) -> bool:
    """Return True for every non-local runtime, including unknown labels."""
    return not is_relaxed_env(env)


def _redis_url_with_default_db(url: str, default_db: int) -> str:
    """Return *url* with ``default_db`` applied without changing hosts.

    ``redis_url_from_env(default_db=1)`` must not jump back to localhost
    when the configured settings URL points at a production Redis host.
    Explicit environment URLs stay authoritative; this only adjusts the
    settings fallback path.
    """
    if default_db == 0:
        return url
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    if parsed.scheme not in {"redis", "rediss"}:
        return url
    return urlunsplit(parsed._replace(path=f"/{default_db}"))


def redis_url_from_env(default_db: int = 0) -> str:
    """Resolve the Redis URL using the app's canonical env var first."""
    default_url = f"redis://localhost:6379/{default_db}"
    env_url = os.getenv("AGENTICORG_REDIS_URL") or os.getenv("REDIS_URL")
    if env_url:
        return env_url
    configured_settings = globals().get("settings")
    settings_url = getattr(configured_settings, "redis_url", default_url)
    return _redis_url_with_default_db(settings_url, default_db)


def redis_socket_timeout_kwargs() -> dict[str, float]:
    """Return bounded Redis socket timeouts, configurable per deploy.

    The defaults intentionally allow more room than the previous 500ms
    cap so real production p99 latency and brief cross-zone jitter do not
    force avoidable in-memory fallback. Deploys can tune these with
    AGENTICORG_REDIS_SOCKET_* env vars.
    """
    configured_settings = globals().get("settings")
    return {
        "socket_connect_timeout": getattr(
            configured_settings, "redis_socket_connect_timeout_seconds", 2.0
        ),
        "socket_timeout": getattr(configured_settings, "redis_socket_timeout_seconds", 2.0),
    }


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
    redis_socket_connect_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    redis_socket_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)

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

    # SEC-012: every environment except local / dev / test is treated
    # as strict. Staging is internet-accessible, used for demos, pilots,
    # and enterprise security review — weak staging secrets become real
    # incidents when staging has production-like integrations.
    _STRICT_ENVS = STRICT_ENVS
    _RELAXED_ENVS = RELAXED_ENVS

    @model_validator(mode="after")
    def validate_production_secret(self) -> Settings:
        """Reject development fallbacks in any non-relaxed environment.

        Strict envs (production, staging, preview) MUST set
        AGENTICORG_SECRET_KEY, AGENTICORG_DB_URL, AGENTICORG_REDIS_URL
        to explicit non-default values. Default placeholders, dev
        credentials, and localhost-pinned URLs all fail closed.

        Secret length is also enforced: minimum 32 chars (≈192 bits of
        entropy) so JWT/HMAC operations don't operate over weak keys.
        """
        is_strict = is_strict_runtime_env(self.env)
        if not is_strict:
            return self

        env_label = self.env  # preserve original casing in error msg
        if self.secret_key == "dev-only-secret-key":
            raise ValueError(
                f"AGENTICORG_SECRET_KEY must be explicitly set in env={env_label!r} "
                "(default 'dev-only-secret-key' is rejected outside local/dev/test)"
            )
        if len(self.secret_key) < 32:
            raise ValueError(
                f"AGENTICORG_SECRET_KEY must be at least 32 chars in env={env_label!r} "
                f"(got {len(self.secret_key)}); JWT/HMAC need ≥192 bits of entropy"
            )
        # Refuse to start with the dev DB URL — would silently use localhost
        # with default credentials, causing data loss or auth bypass.
        if "agenticorg_dev@localhost" in self.db_url:
            raise ValueError(
                f"AGENTICORG_DB_URL must be explicitly set in env={env_label!r} "
                "(detected dev fallback with localhost credentials)"
            )
        if "localhost" in self.redis_url or "127.0.0.1" in self.redis_url:
            raise ValueError(
                f"AGENTICORG_REDIS_URL must be explicitly set in env={env_label!r} "
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
