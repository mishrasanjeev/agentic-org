"""Real-staging runtime guards for Grantex Commerce evals."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from core.security.artifact_paths import ArtifactPathError, resolve_repo_artifact_path

APPROVED_STAGING_ORIGIN = "https://api-staging.grantex.dev"
REPO_ROOT = Path(__file__).resolve().parents[2]
TMP_ROOT = REPO_ROOT / ".tmp"
REPORT_ROOTS = (
    TMP_ROOT,
    REPO_ROOT / "docs" / "reports",
)
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
AUTH_CONFIG_KEYS = {
    "GRANTEX_COMMERCE_BEARER_TOKEN": "bearer_token",
    "GRANTEX_AGENT_ASSERTION": "agent_assertion",
    "GRANTEX_API_KEY": "api_key",
}
SENSITIVE_FIXTURE_ENV_NAMES = frozenset(
    {
        *AUTH_ENV_NAMES,
        "AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT",
        "AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT",
        "AGENTICORG_COMMERCE_REVOKED_PASSPORT_JWT",
        "AGENTICORG_COMMERCE_EXPIRED_PASSPORT_JWT",
        "AGENTICORG_COMMERCE_DENIED_CONSENT_REF",
    }
)
ALLOWED_FIXTURE_ENV_NAMES = frozenset(
    {
        "GRANTEX_COMMERCE_BASE_URL",
        "GRANTEX_BASE_URL",
        "AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL",
        "AGENTICORG_COMMERCE_REAL_STAGING",
        "AGENTICORG_COMMERCE_FIXTURE_VERSION",
        "AGENTICORG_COMMERCE_FIXTURE_PROVIDER",
        "AGENTICORG_COMMERCE_FIXTURE_SYNTHETIC_ONLY",
        "AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID",
        "AGENTICORG_COMMERCE_FIXTURE_AGENT_ID",
        "AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID",
        "AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID",
        "AGENTICORG_COMMERCE_FIXTURE_CURRENCY",
        "AGENTICORG_COMMERCE_FIXTURE_AMOUNT_MINOR_UNITS",
        "AGENTICORG_COMMERCE_FIXTURE_PASSPORT_MAX_AMOUNT_MINOR_UNITS",
        "AGENTICORG_COMMERCE_FIXTURE_AUTH_ENV_NAME",
        *SENSITIVE_FIXTURE_ENV_NAMES,
    }
)


class RealStagingConfigError(ValueError):
    """Raised before auth lookup, connector creation, or network use."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CommerceFixtureConfig:
    env_path: str | None = None
    values: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def variable_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.values))

    @property
    def merchant_id(self) -> str | None:
        return self.values.get("AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID")

    @property
    def agent_id(self) -> str | None:
        return self.values.get("AGENTICORG_COMMERCE_FIXTURE_AGENT_ID")

    @property
    def product_id(self) -> str | None:
        return self.values.get("AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID")

    @property
    def variant_id(self) -> str | None:
        return self.values.get("AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID")

    @property
    def currency(self) -> str:
        return self.values.get("AGENTICORG_COMMERCE_FIXTURE_CURRENCY") or "INR"

    @property
    def amount_minor_units(self) -> int:
        return _int_value(self.values.get("AGENTICORG_COMMERCE_FIXTURE_AMOUNT_MINOR_UNITS"), 0)

    @property
    def amount_minor_units_if_present(self) -> int | None:
        return _optional_int_value(self.values.get("AGENTICORG_COMMERCE_FIXTURE_AMOUNT_MINOR_UNITS"))

    @property
    def passport_max_amount_minor_units(self) -> int:
        return _int_value(self.values.get("AGENTICORG_COMMERCE_FIXTURE_PASSPORT_MAX_AMOUNT_MINOR_UNITS"), 250000)

    @property
    def passport_max_amount_minor_units_if_present(self) -> int | None:
        return _optional_int_value(self.values.get("AGENTICORG_COMMERCE_FIXTURE_PASSPORT_MAX_AMOUNT_MINOR_UNITS"))

    @property
    def positive_payment_amount_cap_relation(self) -> str:
        amount = self.amount_minor_units_if_present
        cap = self.passport_max_amount_minor_units_if_present
        if amount is None or cap is None:
            return "missing_metadata"
        return "within_cap" if amount <= cap else "amount_exceeds_cap"

    @property
    def browse_passport_jwt(self) -> str | None:
        return self.values.get("AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT")

    @property
    def checkout_passport_jwt(self) -> str | None:
        return self.values.get("AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT")

    @property
    def revoked_passport_jwt(self) -> str | None:
        return self.values.get("AGENTICORG_COMMERCE_REVOKED_PASSPORT_JWT")

    @property
    def expired_passport_jwt(self) -> str | None:
        return self.values.get("AGENTICORG_COMMERCE_EXPIRED_PASSPORT_JWT")

    @property
    def denied_consent_ref(self) -> str | None:
        return self.values.get("AGENTICORG_COMMERCE_DENIED_CONSENT_REF")

    @property
    def synthetic_ids(self) -> dict[str, str]:
        names = (
            "AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID",
            "AGENTICORG_COMMERCE_FIXTURE_AGENT_ID",
            "AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID",
            "AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID",
        )
        return {name: self.values[name] for name in names if self.values.get(name)}

    @property
    def sensitive_value_hashes(self) -> tuple[dict[str, str], ...]:
        hashes = []
        for name in sorted(SENSITIVE_FIXTURE_ENV_NAMES):
            value = self.values.get(name)
            if value:
                hashes.append({"name": name, "sha256_12": sha256(value.encode("utf-8")).hexdigest()[:12]})
        return tuple(hashes)


@dataclass(frozen=True)
class RealStagingConfig:
    grantex_base_url: str
    auth_env_name: str
    auth_value: str = field(repr=False)
    auth_config_key: str
    evidence_report: str | None = None
    fixture: CommerceFixtureConfig = field(default_factory=CommerceFixtureConfig)

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


def _int_value(value: str | None, fallback: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return fallback
    return parsed if parsed >= 0 else fallback


def _optional_int_value(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "enabled"}


def _decode_fixture_value(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return text.replace('\\"', '"').replace("\\\\", "\\").strip()


def _resolve_fixture_path(path: str) -> Path:
    try:
        return resolve_repo_artifact_path(
            path,
            repo_root=REPO_ROOT,
            allowed_roots=(TMP_ROOT,),
            field_name="fixture_env",
            outside_reason="outside_tmp",
            direct_child=False,
        )
    except ArtifactPathError as exc:
        raise RealStagingConfigError(exc.code, exc.message) from exc


def _resolve_evidence_report_path(path: str | Path) -> Path:
    try:
        return resolve_repo_artifact_path(
            path,
            repo_root=REPO_ROOT,
            allowed_roots=REPORT_ROOTS,
            field_name="evidence_report",
            outside_reason="outside_report_roots",
            allowed_suffixes=(".md",),
            direct_child=True,
        )
    except ArtifactPathError as exc:
        raise RealStagingConfigError(exc.code, exc.message) from exc


def _load_fixture_env(path: str | None) -> CommerceFixtureConfig:
    if not path:
        return CommerceFixtureConfig()
    fixture_path = _resolve_fixture_path(path)
    if not fixture_path.exists():
        raise RealStagingConfigError("fixture_env_missing", "Commerce real-staging fixture env file was not found.")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(fixture_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise RealStagingConfigError(
                "fixture_env_invalid",
                f"Commerce fixture env line {line_number} must use KEY=value syntax.",
            )
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key not in ALLOWED_FIXTURE_ENV_NAMES:
            raise RealStagingConfigError(
                "fixture_env_key_refused",
                f"Commerce fixture env key is not allowed: {key}",
            )
        values[key] = _decode_fixture_value(raw_value)

    if values.get("AGENTICORG_COMMERCE_FIXTURE_PROVIDER", "mock") != "mock":
        raise RealStagingConfigError("non_mock_fixture_refused", "Commerce fixture env must use mock provider only.")
    if _is_true(values.get("COMMERCE_LIVE_MODE_ENABLED")) or _is_true(values.get("PLURAL_LIVE_ENABLED")):
        raise RealStagingConfigError("live_fixture_refused", "Commerce fixture env cannot enable live payment flags.")

    relative_path = fixture_path.relative_to(REPO_ROOT).as_posix()
    return CommerceFixtureConfig(env_path=relative_path, values=values)


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


def _resolve_auth_value(environ: dict[str, str], auth_env_name: str) -> str:
    return str(environ.get(auth_env_name, "") or "").strip()


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
    fixture_env_path: str | None = None,
    environ: dict[str, str] | None = None,
) -> RealStagingConfig:
    env = environ if environ is not None else os.environ
    fixture = _load_fixture_env(fixture_env_path or env.get("AGENTICORG_COMMERCE_FIXTURE_ENV"))
    merged_env = {**env, **fixture.values}

    if _is_true(merged_env.get("COMMERCE_LIVE_MODE_ENABLED")) or _is_true(merged_env.get("PLURAL_LIVE_ENABLED")):
        raise RealStagingConfigError(
            "live_payment_flags_refused",
            "Real-staging mode refuses live Commerce or live Plural flags.",
        )

    origin = normalize_origin(_resolve_base_url(grantex_base_url, merged_env))
    if origin in REFUSED_PRODUCTION_ORIGINS:
        raise RealStagingConfigError("production_url_refused", "Production URLs are refused in real-staging mode.")

    allowed_smoke_origin = None
    smoke_value = allow_smoke_cloud_run_url or merged_env.get("AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL") or ""
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

    if str(merged_env.get("AGENTICORG_TEST_FAKE_CONNECTORS", "") or "").strip() == "1":
        raise RealStagingConfigError(
            "fake_connectors_refused",
            "Real-staging mode cannot run while fake connector transport is enabled.",
        )

    auth_env_name = _resolve_auth_env_name(merged_env)
    return RealStagingConfig(
        grantex_base_url=origin,
        auth_env_name=auth_env_name,
        auth_value=_resolve_auth_value(merged_env, auth_env_name),
        auth_config_key=AUTH_CONFIG_KEYS[auth_env_name],
        evidence_report=str(_resolve_evidence_report_path(evidence_report)) if evidence_report else None,
        fixture=fixture,
    )
