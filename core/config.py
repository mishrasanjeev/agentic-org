"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from typing import Literal
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
    # Log record rendering (core/logging_config.py): "json" emits one JSON
    # object per line for container log collectors; "console" is the
    # human-readable renderer. ``env=test`` defaults to console so pytest
    # output stays readable unless AGENTICORG_LOG_FORMAT is set explicitly.
    log_format: str = "json"
    secret_key: str = Field(default="dev-only-secret-key", min_length=16)

    # Database
    db_url: str = "postgresql+asyncpg://agenticorg:agenticorg_dev@localhost:5432/agenticorg"
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    # SQLAlchemy statement echo. Opt-in only (AGENTICORG_DB_ECHO=1): it was
    # previously tied to env=development, so every dev container emitted
    # multi-line SQL on stdout.
    db_echo: bool = False

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
    # Grantex bearer tokens are accepted only when ``iss`` matches this issuer
    # and ``aud`` matches this audience (auth/grantex_middleware.py). Audience
    # is mandatory in strict runtimes. Env: AGENTICORG_GRANTEX_ISSUER/_AUDIENCE.
    grantex_issuer: str = ""
    grantex_audience: str = ""
    # route_meta enforcement (api/route_enforcement.py): "enforce" | "log"
    route_enforcement_mode: str = "enforce"
    jwt_public_key_url: str = ""
    jwt_issuer: str = ""  # Grantex token server issuer URI (AGENTICORG_JWT_ISSUER)
    token_ttl_minutes: int = 60
    # Per-IP throttles key on the first X-Forwarded-For hop instead of the
    # socket peer (api/client_ip.py). Enable ONLY behind a trusted reverse
    # proxy (Cloud Run / nginx) that overwrites the header; otherwise the
    # throttle bucket is client-spoofable. Env: AGENTICORG_TRUST_PROXY_HEADERS.
    trust_proxy_headers: bool = False

    # Google OAuth
    google_oauth_client_id: str = ""  # Google Cloud Console OAuth 2.0 Client ID

    # CORS
    cors_allowed_origins: str = ""  # Comma-separated origins; empty = allow all in dev

    # Public-facing API base URL — single source of truth for OAuth redirect
    # URIs. Required for every non-relaxed env because Cloud Run terminates
    # TLS at the edge, so ``request.url_for`` inside the container returns
    # ``http://`` and the resulting redirect_uri never matches the
    # ``https://`` value registered on Zoho / Google / etc.
    # Must include scheme + host (and optionally a path prefix).
    public_api_base_url: str = ""

    # Public URL of the web UI (scheme + host, optional path prefix, no
    # trailing slash). Used to build browser redirects after SSO login when
    # the UI is served from a different origin than the API (Cloud Run splits
    # them). Empty means "same origin as the API" (local docker / dev proxy).
    # Env: AGENTICORG_UI_BASE_URL.
    ui_base_url: str = ""

    # Entry-point plugins (connectors/plugins.py). Off by default: loading a
    # plugin runs third-party code, so only allowlisted distributions load.
    # Env: AGENTICORG_PLUGIN_LOADING, AGENTICORG_PLUGIN_ALLOWLIST (comma list).
    plugin_loading: bool = False
    plugin_allowlist: str = ""

    # Grant enforcement on agent tool calls (auth/grant_enforcement.py,
    # docs/operations/grant-enforcement.md). Deployment default for
    # ``grants.enforce_closed``: ``off`` keeps the legacy behaviour, ``warn``
    # allows and records every call that would be denied, ``deny`` refuses it.
    # Tenants override it with the ``grants.enforce_closed.warn`` and
    # ``grants.enforce_closed.deny`` feature flags. An unknown value fails
    # startup. Env: AGENTICORG_GRANTS_ENFORCE_CLOSED.
    grants_enforce_closed: Literal["off", "warn", "deny"] = "off"
    # Lifetime requested for a per-run grant delegated from the root grant
    # (auth/token_pool.py). Env: AGENTICORG_GRANTS_RUN_TOKEN_TTL_SECONDS.
    grants_run_token_ttl_seconds: int = Field(default=900, ge=60, le=86_400)

    # Platform behaviour
    pii_masking: bool = True
    data_region: str = "IN"
    audit_retention_years: int = 7
    max_concurrent_workflows: int = 500
    default_hitl_threshold_inr: int = 500_000
    default_confidence_floor: float = 0.88
    max_agent_retries: int = 3

    # Resource-intensive local runtimes. These per-worker caps prevent one API
    # process from spawning unbounded Tesseract and Chromium processes.
    document_extraction_max_concurrency: int = Field(default=2, ge=1, le=32)
    document_extraction_queue_timeout_seconds: float = Field(default=30.0, ge=0.1, le=300.0)
    rpa_max_concurrency: int = Field(default=2, ge=1, le=16)
    rpa_queue_timeout_seconds: float = Field(default=5.0, ge=0.1, le=120.0)

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
        # Uday CA-Firms 2026-05-14: OAuth redirect_uri was being computed
        # from ``request.url_for`` which returns ``http://`` on Cloud Run
        # (TLS terminated at the edge). When set, the value MUST be https
        # so the redirect URI we send to Zoho / Google / etc. matches what
        # the provider has registered. We do NOT block strict-env startup
        # on a missing value — the OAuth handler has a proxy-aware
        # fallback path that upgrades to https — but we DO refuse a
        # half-configured non-https value, which would silently downgrade.
        if self.public_api_base_url:
            parsed = urlsplit(self.public_api_base_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError(
                    "AGENTICORG_PUBLIC_API_BASE_URL must start with https:// "
                    "and include a host. Got: "
                    f"{self.public_api_base_url!r}"
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
    # Root grant the platform delegates per-run agent grants from
    # (auth/token_pool.py). A credential: set it from a secret manager.
    # Env: GRANTEX_ROOT_GRANT_TOKEN.
    grantex_root_grant_token: str = ""
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


GRANTEX_PRODUCTION_BASE_URL = "https://api.grantex.dev"
GRANTEX_STAGING_BASE_URL = "https://api-staging.grantex.dev"
GRANTEX_PRODUCTION_ENVS = frozenset({"production", "prod"})
# Hosted non-production runtimes verify grant tokens against the Grantex
# staging issuer: every STRICT_ENVS label that is not production, plus
# ``uat`` (not listed in STRICT_ENVS, but ``is_strict_runtime_env`` treats
# it as a hosted runtime). Production, local/dev/test and unknown labels use
# the production issuer, so an unrecognised env never accepts staging tokens.
GRANTEX_STAGING_ENVS = (STRICT_ENVS - GRANTEX_PRODUCTION_ENVS) | frozenset({"uat"})


def grantex_base_url_for_env(env: str | None = None) -> str:
    """Return the Grantex API origin (SDK base URL and JWKS host).

    An explicit ``GRANTEX_BASE_URL`` (env var or ``.env``) wins; otherwise
    staging-like environments use the Grantex staging origin and everything
    else the production origin. Before bug sheet 2026-09-14 #13 every
    environment silently resolved to the production issuer.
    """
    explicit = os.getenv("GRANTEX_BASE_URL", "").strip() or (
        external_keys.grantex_base_url if "grantex_base_url" in external_keys.model_fields_set else ""
    )
    if explicit:
        return explicit.strip().rstrip("/")
    label = normalize_env(env if env is not None else settings.env)
    if label in GRANTEX_STAGING_ENVS and is_strict_runtime_env(label):
        return GRANTEX_STAGING_BASE_URL
    return GRANTEX_PRODUCTION_BASE_URL


def grantex_jwks_uri_for_env(env: str | None = None) -> str:
    """Return the JWKS URI grant tokens are verified against."""
    return f"{grantex_base_url_for_env(env)}/.well-known/jwks.json"


def grantex_issuer_for_env(env: str | None = None) -> str:
    """Return the expected ``iss`` claim for Grantex grant tokens.

    Precedence: ``AGENTICORG_GRANTEX_ISSUER`` (settings or raw env var), then
    the issuer the Grantex SDK derives from the per-environment JWKS URI
    (``GRANTEX_BASE_URL`` if set, else the env default above). Returns ``""``
    when the SDK is unavailable so callers fail closed.
    """
    configured_settings = globals().get("settings")
    configured = (
        str(getattr(configured_settings, "grantex_issuer", "") or "").strip()
        or os.getenv("AGENTICORG_GRANTEX_ISSUER", "").strip()
    )
    if configured:
        return configured.rstrip("/")
    try:
        from grantex._verify import _derive_issuer_from_jwks_uri
    # enterprise-gate: broad-except-ok reason=missing-grantex-sdk-does-not-enable-grantex-mode-empty-issuer-fails-closed
    except Exception:
        return ""
    return str(_derive_issuer_from_jwks_uri(grantex_jwks_uri_for_env(env))).rstrip("/")
