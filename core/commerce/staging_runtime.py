"""Real-staging runtime guards for Grantex Commerce evals."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

APPROVED_STAGING_ORIGIN = "https://api-staging.grantex.dev"
REFUSED_PRODUCTION_ORIGINS = frozenset(
    {
        "https://api.grantex.dev",
        "https://grantex.dev",
        "https://app.agenticorg.ai",
    }
)
AUTH_ENV_NAMES = (
    "GRANTEX_COMMERCE_BEARER_TOKEN",
    "GRANTEX_AGENT_ASSERTION",
    "GRANTEX_API_KEY",
)


class RealStagingConfigError(ValueError):
    """Raised before auth lookup, connector creation, or network use."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RealStagingConfig:
    grantex_base_url: str
    auth_env_name: str
    evidence_report: str | None = None

    @property
    def grantex_host(self) -> str:
        return urlparse(self.grantex_base_url).hostname or ""


def normalize_origin(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise RealStagingConfigError("invalid_staging_url", "A complete Grantex staging URL is required.")
    if parsed.username or parsed.password:
        raise RealStagingConfigError("credentialed_url_refused", "Credentialed URLs are not allowed.")
    if parsed.scheme != "https":
        raise RealStagingConfigError("non_https_url_refused", "Real-staging mode requires HTTPS.")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise RealStagingConfigError("non_origin_url_refused", "Use only the Grantex staging URL origin.")
    hostname = parsed.hostname or ""
    return f"{parsed.scheme}://{hostname}{f':{parsed.port}' if parsed.port else ''}"


def _resolve_auth_env_name(environ: dict[str, str]) -> str:
    present = [name for name in AUTH_ENV_NAMES if str(environ.get(name, "") or "").strip()]
    if not present:
        raise RealStagingConfigError(
            "staging_auth_required",
            "Set exactly one Grantex staging auth env var by name before real-staging runs.",
        )
    if len(present) > 1:
        raise RealStagingConfigError(
            "ambiguous_staging_auth",
            "Set exactly one Grantex staging auth env var for real-staging runs.",
        )
    return present[0]


def _resolve_base_url(cli_base_url: str | None, environ: dict[str, str]) -> str:
    value = (
        cli_base_url
        or environ.get("GRANTEX_COMMERCE_BASE_URL")
        or environ.get("GRANTEX_BASE_URL")
        or ""
    ).strip()
    if not value:
        raise RealStagingConfigError(
            "staging_url_required",
            "Set GRANTEX_COMMERCE_BASE_URL or pass --grantex-base for real-staging mode.",
        )
    return value


def validate_real_staging_config(
    *,
    grantex_base_url: str | None = None,
    allow_smoke_cloud_run_url: str | None = None,
    evidence_report: str | None = None,
    environ: dict[str, str] | None = None,
) -> RealStagingConfig:
    env = environ if environ is not None else os.environ

    origin = normalize_origin(_resolve_base_url(grantex_base_url, env))
    if origin in REFUSED_PRODUCTION_ORIGINS:
        raise RealStagingConfigError("production_url_refused", "Production URLs are refused in real-staging mode.")

    allowed_smoke_origin = None
    smoke_value = allow_smoke_cloud_run_url or env.get("AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL") or ""
    if smoke_value.strip():
        allowed_smoke_origin = normalize_origin(smoke_value)

    host = urlparse(origin).hostname or ""
    if host.endswith(".run.app"):
        if origin != allowed_smoke_origin:
            raise RealStagingConfigError(
                "smoke_url_not_allowlisted",
                "Cloud Run smoke URLs must be exactly allowlisted before real-staging runs.",
            )
    elif origin != APPROVED_STAGING_ORIGIN:
        raise RealStagingConfigError(
            "staging_url_not_approved",
            "Real-staging mode only accepts the approved Grantex staging URL or an exact smoke URL.",
        )

    if str(env.get("AGENTICORG_TEST_FAKE_CONNECTORS", "") or "").strip() == "1":
        raise RealStagingConfigError(
            "fake_connectors_refused",
            "Real-staging mode cannot run while fake connector transport is enabled.",
        )

    return RealStagingConfig(
        grantex_base_url=origin,
        auth_env_name=_resolve_auth_env_name(env),
        evidence_report=evidence_report,
    )
