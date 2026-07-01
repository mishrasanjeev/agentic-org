"""Fail-closed C3 hosted AgenticOrg commerce smoke runner.

This runner validates a temporary API-only AgenticOrg Cloud Run smoke service
and records redacted discovery evidence. It never creates resources, never
deploys, and never reads cloud secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.security.artifact_paths import (  # noqa: E402
    ArtifactPathError,
    atomic_write_text_artifact,
    resolve_repo_artifact_path,
)

EXPECTED_CONSENT_EXCHANGE_BLOCKER = "preexported_checkout_passport_without_granted_consent_fixture"

AUTH_SOURCE_ENV_NAMES = (
    "GRANTEX_COMMERCE_BEARER_TOKEN",
    "GRANTEX_AGENT_ASSERTION",
    "GRANTEX_API_KEY",
)
SENSITIVE_FIXTURE_ENV_NAMES = frozenset(
    {
        *AUTH_SOURCE_ENV_NAMES,
        "AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT",
        "AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT",
        "AGENTICORG_COMMERCE_REVOKED_PASSPORT_JWT",
        "AGENTICORG_COMMERCE_EXPIRED_PASSPORT_JWT",
        "AGENTICORG_COMMERCE_DENIED_CONSENT_REF",
    }
)
SYNTHETIC_ID_ENV_NAMES = frozenset(
    {
        "AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID",
        "AGENTICORG_COMMERCE_FIXTURE_AGENT_ID",
        "AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID",
        "AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID",
    }
)

PRODUCTION_AGENTICORG_ORIGINS = frozenset(
    {
        "https://app.agenticorg.ai",
        "https://agenticorg.ai",
        "https://www.agenticorg.ai",
    }
)
PRODUCTION_GRANTEX_ORIGINS = frozenset({"https://api.grantex.dev", "https://grantex.dev"})
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
PRODUCTION_RESOURCE_NAMES = frozenset(
    {
        "agenticorg-api",
        "agenticorg-ui",
        "agenticorg-worker",
        "agenticorg-beat",
        "agenticorg-migrate",
        "agenticorg-pg",
        "agenticorg-redis",
        "agenticorg-secret-key",
        "agenticorg-database-url",
        "agenticorg-redis-url",
        "grantex-api-key",
        "grantex-agent-assertion",
        "grantex-commerce-bearer-token",
    }
)
DEFAULT_SMOKE_BINDING_NAMES = (
    "agenticorg-commerce-smoke-secret-key",
    "agenticorg-commerce-smoke-db-url",
    "agenticorg-commerce-smoke-redis-url",
    "agenticorg-commerce-smoke-grantex-api-key",
)
DEFAULT_PUBLIC_ENV_NAMES = (
    "AGENTICORG_ENV",
    "AGENTICORG_BASE_URL",
    "AGENTICORG_PUBLIC_API_BASE_URL",
    "AGENTICORG_CORS_ALLOWED_ORIGINS",
    "AGENTICORG_GIT_SHA",
    "AGENTICORG_ENABLE_LEGACY_STARTUP_DDL",
    "AGENTICORG_COMMERCE_REAL_STAGING",
    "GRANTEX_COMMERCE_BASE_URL",
    "GRANTEX_BASE_URL",
    "AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL",
    "COMMERCE_LIVE_MODE_ENABLED",
    "PLURAL_LIVE_ENABLED",
    "PLURAL_ENV",
)
TMP_ROOT = REPO_ROOT / ".tmp"
REPORT_ROOTS = (
    TMP_ROOT,
    REPO_ROOT / "docs" / "reports",
)


class HostedSmokeConfigError(ValueError):
    """Raised before network use when C3 smoke guardrails are not met."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FixtureSummary:
    env_path: str | None = None
    fixture_binding_name: str | None = None
    variable_names: tuple[str, ...] = ()
    synthetic_ids: dict[str, str] = field(default_factory=dict)
    sensitive_value_hashes: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class HostedSmokeConfig:
    agenticorg_base_url: str
    grantex_base_url: str
    auth_source_env_name: str
    allow_agenticorg_cloud_run_url: str | None = None
    allow_grantex_cloud_run_url: str | None = None
    agenticorg_service_name: str = "agenticorg-api-commerce-smoke"
    eval_job_name: str = "agenticorg-commerce-smoke-eval"
    migrate_job_name: str = "agenticorg-commerce-smoke-migrate"
    database_resource_name: str = "agenticorg-commerce-smoke-pg"
    redis_resource_name: str = "agenticorg-commerce-smoke-redis"
    smoke_binding_names: tuple[str, ...] = DEFAULT_SMOKE_BINDING_NAMES
    public_env_names: tuple[str, ...] = DEFAULT_PUBLIC_ENV_NAMES
    commit_sha: str | None = None
    image_tag: str | None = None
    cleanup_by: str | None = None
    evidence_report: str | None = None
    real_staging_evidence_report: str | None = None
    fixture: FixtureSummary = field(default_factory=FixtureSummary)

    @property
    def agenticorg_host(self) -> str:
        return urlparse(self.agenticorg_base_url).hostname or ""

    @property
    def grantex_host(self) -> str:
        return urlparse(self.grantex_base_url).hostname or ""


@dataclass
class SmokeCase:
    name: str
    status: str
    http_status: int | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    blocker: str | None = None


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "enabled", "on"}


def _normalize_origin(raw_url: str, *, code_prefix: str) -> str:
    value = str(raw_url or "").strip()
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise HostedSmokeConfigError(f"{code_prefix}_url_invalid", "A complete HTTPS origin is required.")
    if parsed.username or parsed.password:
        raise HostedSmokeConfigError(f"{code_prefix}_credentialed_url_refused", "Credentialed URLs are refused.")
    if parsed.scheme != "https":
        raise HostedSmokeConfigError(f"{code_prefix}_non_https_url_refused", "C3 hosted smoke requires HTTPS.")
    hostname = parsed.hostname or ""
    if hostname.lower() in LOCAL_HOSTS:
        raise HostedSmokeConfigError(f"{code_prefix}_localhost_refused", "Localhost is refused for hosted smoke.")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise HostedSmokeConfigError(f"{code_prefix}_non_origin_url_refused", "Use only the URL origin.")
    return f"{parsed.scheme}://{hostname}{f':{parsed.port}' if parsed.port else ''}"


def _validate_run_app_allowlist(origin: str, allow_origin: str | None, *, code_prefix: str) -> str | None:
    host = urlparse(origin).hostname or ""
    normalized_allow = None
    if allow_origin:
        normalized_allow = _normalize_origin(allow_origin, code_prefix=f"{code_prefix}_allowlist")
    if host.endswith(".run.app") and origin != normalized_allow:
        raise HostedSmokeConfigError(
            f"{code_prefix}_smoke_url_not_allowlisted",
            "Cloud Run smoke URLs must exactly match their allowlist value.",
        )
    return normalized_allow


def _validate_not_production(origin: str, *, production_origins: frozenset[str], code_prefix: str) -> None:
    if origin in production_origins:
        raise HostedSmokeConfigError(f"{code_prefix}_production_url_refused", "Production URLs are refused.")


def _validate_smoke_name(name: str, *, field_name: str) -> str:
    normalized = str(name or "").strip()
    lowered = normalized.lower()
    if not normalized:
        raise HostedSmokeConfigError("smoke_resource_name_missing", f"{field_name} is required.")
    if lowered in PRODUCTION_RESOURCE_NAMES or "prod" in lowered or "production" in lowered:
        raise HostedSmokeConfigError(
            "production_resource_name_refused",
            f"{field_name} must not use a production-looking resource name.",
        )
    if "smoke" not in lowered:
        raise HostedSmokeConfigError("smoke_resource_name_required", f"{field_name} must contain 'smoke'.")
    return normalized


def _decode_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.replace('\\"', '"').replace("\\\\", "\\").strip()


def _resolve_tmp_fixture_path(path: str) -> Path:
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
        raise HostedSmokeConfigError(exc.code, exc.message) from exc


def _resolve_report_path(path: str | Path, *, field_name: str) -> Path:
    try:
        return resolve_repo_artifact_path(
            path,
            repo_root=REPO_ROOT,
            allowed_roots=REPORT_ROOTS,
            field_name=field_name,
            outside_reason="outside_report_roots",
            allowed_suffixes=(".md",),
            direct_child=True,
        )
    except ArtifactPathError as exc:
        raise HostedSmokeConfigError(exc.code, exc.message) from exc


def _summarize_fixture_env(path: str, *, auth_source_env_name: str) -> FixtureSummary:
    fixture_path = _resolve_tmp_fixture_path(path)
    if not fixture_path.exists():
        raise HostedSmokeConfigError("fixture_env_missing", "Fixture env file was not found.")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(fixture_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise HostedSmokeConfigError(
                "fixture_env_invalid",
                f"Fixture env line {line_number} must use KEY=value syntax.",
            )
        key, raw_value = line.split("=", 1)
        values[key.strip()] = _decode_env_value(raw_value)

    present_auth_sources = [name for name in AUTH_SOURCE_ENV_NAMES if values.get(name)]
    if len(present_auth_sources) != 1:
        raise HostedSmokeConfigError(
            "fixture_auth_source_count_invalid",
            "Fixture env must include exactly one Grantex auth source value.",
        )
    if present_auth_sources[0] != auth_source_env_name:
        raise HostedSmokeConfigError(
            "fixture_auth_source_mismatch",
            "Fixture auth source must match --auth-source-env-name.",
        )

    hashes = []
    for name in sorted(SENSITIVE_FIXTURE_ENV_NAMES):
        value = values.get(name)
        if value:
            hashes.append({"name": name, "sha256_12": sha256(value.encode("utf-8")).hexdigest()[:12]})

    synthetic_ids = {name: values[name] for name in sorted(SYNTHETIC_ID_ENV_NAMES) if values.get(name)}
    relative_path = fixture_path.relative_to(REPO_ROOT).as_posix()
    return FixtureSummary(
        env_path=relative_path,
        variable_names=tuple(sorted(values)),
        synthetic_ids=synthetic_ids,
        sensitive_value_hashes=tuple(hashes),
    )


def validate_config(args: argparse.Namespace, *, environ: dict[str, str] | None = None) -> HostedSmokeConfig:
    env = environ if environ is not None else os.environ
    agenticorg_origin = _normalize_origin(args.agenticorg_base, code_prefix="agenticorg")
    grantex_origin = _normalize_origin(args.grantex_base, code_prefix="grantex")
    _validate_not_production(
        agenticorg_origin,
        production_origins=PRODUCTION_AGENTICORG_ORIGINS,
        code_prefix="agenticorg",
    )
    _validate_not_production(grantex_origin, production_origins=PRODUCTION_GRANTEX_ORIGINS, code_prefix="grantex")
    allow_agenticorg = _validate_run_app_allowlist(
        agenticorg_origin,
        args.allow_agenticorg_cloud_run_url,
        code_prefix="agenticorg",
    )
    allow_grantex = _validate_run_app_allowlist(
        grantex_origin,
        args.allow_grantex_cloud_run_url,
        code_prefix="grantex",
    )

    if _truthy(env.get("COMMERCE_LIVE_MODE_ENABLED")) or _truthy(env.get("PLURAL_LIVE_ENABLED")):
        raise HostedSmokeConfigError("live_flags_refused", "Live Commerce and live Plural flags must be false.")
    if args.commerce_live_mode or args.plural_live_mode:
        raise HostedSmokeConfigError("live_flags_refused", "Live Commerce and live Plural flags must be false.")

    auth_source_env_name = str(args.auth_source_env_name or "").strip()
    if auth_source_env_name not in AUTH_SOURCE_ENV_NAMES:
        raise HostedSmokeConfigError("auth_source_env_name_invalid", "Use exactly one supported Grantex auth source name.")

    if args.fixture_env and args.fixture_binding_name:
        raise HostedSmokeConfigError(
            "fixture_source_ambiguous",
            "Use either a local .tmp fixture env or a smoke-only fixture secret name, not both.",
        )

    fixture = FixtureSummary()
    if args.fixture_env:
        fixture = _summarize_fixture_env(args.fixture_env, auth_source_env_name=auth_source_env_name)
    elif args.fixture_binding_name:
        fixture = FixtureSummary(
            fixture_binding_name=_validate_smoke_name(args.fixture_binding_name, field_name="fixture binding")
        )

    smoke_binding_names = tuple(
        _validate_smoke_name(name, field_name="protected binding") for name in args.smoke_binding_name
    )
    evidence_report = (
        str(_resolve_report_path(args.evidence_report, field_name="evidence_report"))
        if args.evidence_report
        else None
    )
    real_staging_evidence_report = (
        str(_resolve_report_path(args.real_staging_evidence_report, field_name="real_staging_evidence_report"))
        if args.real_staging_evidence_report
        else None
    )

    return HostedSmokeConfig(
        agenticorg_base_url=agenticorg_origin,
        grantex_base_url=grantex_origin,
        auth_source_env_name=auth_source_env_name,
        allow_agenticorg_cloud_run_url=allow_agenticorg,
        allow_grantex_cloud_run_url=allow_grantex,
        agenticorg_service_name=_validate_smoke_name(args.agenticorg_service, field_name="AgenticOrg service"),
        eval_job_name=_validate_smoke_name(args.eval_job, field_name="eval job"),
        migrate_job_name=_validate_smoke_name(args.migrate_job, field_name="migrate job"),
        database_resource_name=_validate_smoke_name(args.database_resource, field_name="database resource"),
        redis_resource_name=_validate_smoke_name(args.redis_resource, field_name="redis resource"),
        smoke_binding_names=smoke_binding_names,
        commit_sha=args.commit_sha,
        image_tag=args.image_tag,
        cleanup_by=args.cleanup_by,
        evidence_report=evidence_report,
        real_staging_evidence_report=real_staging_evidence_report,
        fixture=fixture,
    )


def _case_from_response(name: str, response: httpx.Response, started_at: float) -> tuple[SmokeCase, Any]:
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return (
            SmokeCase(
                name=name,
                status="fail",
                http_status=response.status_code,
                latency_ms=latency_ms,
                error_code="invalid_json",
            ),
            None,
        )
    status = "pass" if response.status_code < 400 else "fail"
    error_code = None if status == "pass" else f"http_{response.status_code}"
    return SmokeCase(name=name, status=status, http_status=response.status_code, latency_ms=latency_ms, error_code=error_code), payload


def _get_json(client: httpx.Client, base_url: str, path: str, *, case_name: str) -> tuple[SmokeCase, Any]:
    started_at = time.perf_counter()
    try:
        response = client.get(f"{base_url}{path}")
    except httpx.HTTPError:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        return SmokeCase(name=case_name, status="fail", latency_ms=latency_ms, error_code="transport_error"), None
    return _case_from_response(case_name, response, started_at)


def _append_assertion_case(cases: list[SmokeCase], *, name: str, condition: bool, error_code: str) -> None:
    cases.append(SmokeCase(name=name, status="pass" if condition else "fail", error_code=None if condition else error_code))


def run_hosted_checks(config: HostedSmokeConfig, *, client: httpx.Client | None = None) -> list[SmokeCase]:
    close_client = client is None
    client = client or httpx.Client(timeout=10.0)
    cases: list[SmokeCase] = []
    try:
        case, liveness = _get_json(client, config.agenticorg_base_url, "/api/v1/health/liveness", case_name="liveness")
        cases.append(case)
        if case.status == "pass":
            _append_assertion_case(
                cases,
                name="liveness_status_alive",
                condition=isinstance(liveness, dict) and liveness.get("status") == "alive",
                error_code="liveness_not_alive",
            )

        case, health = _get_json(client, config.agenticorg_base_url, "/api/v1/health", case_name="health")
        cases.append(case)
        if case.status == "pass":
            _append_assertion_case(
                cases,
                name="health_status_healthy",
                condition=isinstance(health, dict) and health.get("status") == "healthy",
                error_code="health_not_healthy",
            )

        case, mcp_tools = _get_json(client, config.agenticorg_base_url, "/api/v1/mcp/tools", case_name="mcp_tools")
        cases.append(case)
        if case.status == "pass":
            tools = mcp_tools.get("tools", []) if isinstance(mcp_tools, dict) else []
            _append_assertion_case(
                cases,
                name="mcp_commerce_sales_agent_discovery",
                condition=any(tool.get("name") == "agenticorg_commerce_sales_agent" for tool in tools if isinstance(tool, dict)),
                error_code="commerce_mcp_tool_missing",
            )

        case, agent_card = _get_json(
            client,
            config.agenticorg_base_url,
            "/api/v1/a2a/.well-known/agent.json",
            case_name="a2a_agent_card",
        )
        cases.append(case)
        if case.status == "pass" and isinstance(agent_card, dict):
            auth = agent_card.get("authentication", {}) if isinstance(agent_card.get("authentication"), dict) else {}
            _append_assertion_case(
                cases,
                name="a2a_card_uses_agenticorg_smoke_origin",
                condition=str(agent_card.get("url", "")).startswith(f"{config.agenticorg_base_url}/api/v1/a2a"),
                error_code="a2a_card_agenticorg_origin_mismatch",
            )
            _append_assertion_case(
                cases,
                name="a2a_card_uses_grantex_smoke_issuer",
                condition=auth.get("issuer") == config.grantex_base_url,
                error_code="a2a_card_grantex_issuer_mismatch",
            )
            _append_assertion_case(
                cases,
                name="a2a_card_uses_grantex_smoke_jwks",
                condition=auth.get("jwksUri") == f"{config.grantex_base_url}/.well-known/jwks.json",
                error_code="a2a_card_grantex_jwks_mismatch",
            )

        case, a2a_agents = _get_json(client, config.agenticorg_base_url, "/api/v1/a2a/agents", case_name="a2a_agents")
        cases.append(case)
        if case.status == "pass":
            agents = a2a_agents.get("agents", []) if isinstance(a2a_agents, dict) else []
            commerce_agent = next(
                (agent for agent in agents if isinstance(agent, dict) and agent.get("id") == "commerce_sales_agent"),
                None,
            )
            tools = commerce_agent.get("tools", []) if isinstance(commerce_agent, dict) else []
            _append_assertion_case(
                cases,
                name="a2a_commerce_sales_agent_discovery",
                condition=commerce_agent is not None,
                error_code="commerce_a2a_agent_missing",
            )
            _append_assertion_case(
                cases,
                name="a2a_commerce_tools_grantex_only",
                condition=bool(tools) and all(str(tool).startswith("grantex_commerce:") for tool in tools),
                error_code="commerce_a2a_tools_not_grantex_only",
            )

        if config.real_staging_evidence_report:
            cases.append(validate_consent_exchange_evidence(Path(config.real_staging_evidence_report)))
    finally:
        if close_client:
            client.close()
    return cases


def validate_consent_exchange_evidence(path: Path) -> SmokeCase:
    if not path.exists():
        return SmokeCase(
            name="consent_exchange_expected_skip_evidence",
            status="fail",
            error_code="real_staging_evidence_missing",
        )
    text = path.read_text(encoding="utf-8")
    consent_lines = [line for line in text.splitlines() if "consent_exchange" in line]
    if any("| consent_exchange | failed |" in line or "| consent_exchange | fail |" in line for line in consent_lines):
        return SmokeCase(
            name="consent_exchange_expected_skip_evidence",
            status="fail",
            error_code="consent_exchange_reported_failed",
        )
    expected = [line for line in consent_lines if "| consent_exchange | skipped |" in line]
    if not any(EXPECTED_CONSENT_EXCHANGE_BLOCKER in line for line in expected):
        return SmokeCase(
            name="consent_exchange_expected_skip_evidence",
            status="fail",
            error_code="consent_exchange_expected_blocker_missing",
        )
    return SmokeCase(name="consent_exchange_expected_skip_evidence", status="pass")


def evidence_as_dict(config: HostedSmokeConfig, cases: list[SmokeCase], *, dry_run: bool) -> dict[str, Any]:
    fixture_source = None
    if config.fixture.env_path:
        fixture_source = "local_tmp_fixture_env"
    elif config.fixture.fixture_binding_name:
        fixture_source = "smoke_binding"
    return {
        "report_type": "agenticorg-c3-hosted-commerce-smoke",
        "run_mode": "dry-run" if dry_run else "run",
        "agenticorg_host": config.agenticorg_host,
        "grantex_host": config.grantex_host,
        "commit_sha": config.commit_sha,
        "image_tag": config.image_tag,
        "cleanup_by": config.cleanup_by,
        "resources": {
            "agenticorg_service_name": config.agenticorg_service_name,
            "eval_job_name": config.eval_job_name,
            "migrate_job_name": config.migrate_job_name,
            "database_resource_name": config.database_resource_name,
            "redis_resource_name": config.redis_resource_name,
        },
        "public_env_var_names": list(config.public_env_names),
        "auth_source_env_name": config.auth_source_env_name,
        "smoke_binding_names": list(config.smoke_binding_names),
        "fixture": {
            "source": fixture_source,
            "fixture_binding_name": config.fixture.fixture_binding_name,
            "env_var_names": list(config.fixture.variable_names),
            "synthetic_ids": config.fixture.synthetic_ids,
            "sensitive_value_hashes": list(config.fixture.sensitive_value_hashes),
        },
        "cases": [case.__dict__ for case in cases],
        "no_provider_call_confirmation": True,
        "redaction": {
            "secret_values_recorded": False,
            "bearer_tokens_recorded": False,
            "passport_values_recorded": False,
            "idempotency_values_recorded": False,
            "provider_material_recorded": False,
            "raw_payloads_recorded": False,
            "db_redis_urls_recorded": False,
            "private_keys_recorded": False,
        },
    }


def write_evidence_report(config: HostedSmokeConfig, cases: list[SmokeCase], *, dry_run: bool, path: str | Path) -> None:
    report_path = _resolve_report_path(path, field_name="evidence_report")
    rows = [
        "| Case | Status | HTTP | Latency ms | Error | Blocker |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in cases:
        rows.append(
            "| "
            + " | ".join(
                [
                    case.name,
                    case.status,
                    "" if case.http_status is None else str(case.http_status),
                    "" if case.latency_ms is None else str(case.latency_ms),
                    case.error_code or "",
                    case.blocker or "",
                ]
            )
            + " |"
        )
    data = evidence_as_dict(config, cases, dry_run=dry_run)
    content = "\n".join(
        [
            "# AgenticOrg C3 Hosted Commerce Smoke Evidence",
            "",
            f"- Run mode: `{data['run_mode']}`",
            f"- AgenticOrg host: `{data['agenticorg_host']}`",
            f"- Grantex host: `{data['grantex_host']}`",
            f"- Auth source env name: `{config.auth_source_env_name}`",
            f"- Fixture source: `{data['fixture']['source'] or ''}`",
            f"- Fixture binding name: `{config.fixture.fixture_binding_name or ''}`",
            "- Secret values recorded: false",
            "- Raw passports/JWTs recorded: false",
            "- Idempotency values recorded: false",
            "- Provider material recorded: false",
            "- Raw request/response bodies recorded: false",
            "- DB/Redis URLs recorded: false",
            "",
            "## Case Results",
            "",
            *rows,
            "",
            "## Redacted Summary",
            "",
            "```json",
            json.dumps(data, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    try:
        atomic_write_text_artifact(report_path, content, encoding="utf-8", repo_root=REPO_ROOT)
    except ArtifactPathError as exc:
        raise HostedSmokeConfigError(exc.code, exc.message) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C3 hosted AgenticOrg commerce smoke runner")
    parser.add_argument("--run", action="store_true", help="Perform hosted HTTP checks. Omit for dry-run only.")
    parser.add_argument("--agenticorg-base", required=True, help="Temporary AgenticOrg API smoke origin")
    parser.add_argument("--grantex-base", required=True, help="Temporary Grantex Option A smoke origin")
    parser.add_argument("--allow-agenticorg-cloud-run-url", default=None)
    parser.add_argument("--allow-grantex-cloud-run-url", default=None)
    parser.add_argument("--auth-source-env-name", required=True, choices=AUTH_SOURCE_ENV_NAMES)
    parser.add_argument("--agenticorg-service", default="agenticorg-api-commerce-smoke")
    parser.add_argument("--eval-job", default="agenticorg-commerce-smoke-eval")
    parser.add_argument("--migrate-job", default="agenticorg-commerce-smoke-migrate")
    parser.add_argument("--database-resource", default="agenticorg-commerce-smoke-pg")
    parser.add_argument("--redis-resource", default="agenticorg-commerce-smoke-redis")
    parser.add_argument(
        "--secret-name",
        dest="smoke_binding_name",
        action="append",
        default=list(DEFAULT_SMOKE_BINDING_NAMES),
    )
    parser.add_argument("--fixture-env", default=None)
    parser.add_argument("--fixture-secret-name", dest="fixture_binding_name", default=None)
    parser.add_argument("--real-staging-evidence-report", default=None)
    parser.add_argument("--evidence-report", default=None)
    parser.add_argument("--commit-sha", default=None)
    parser.add_argument("--image-tag", default=None)
    parser.add_argument("--cleanup-by", default=None)
    parser.add_argument("--commerce-live-mode", action="store_true")
    parser.add_argument("--plural-live-mode", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = validate_config(args)
        cases: list[SmokeCase] = []
        if args.run:
            cases = run_hosted_checks(config)
            if config.evidence_report:
                write_evidence_report(config, cases, dry_run=False, path=config.evidence_report)
        else:
            print(json.dumps(evidence_as_dict(config, cases, dry_run=True), indent=2, sort_keys=True))
        return 0 if all(case.status != "fail" for case in cases) else 1
    except HostedSmokeConfigError as exc:
        print(f"fail_closed: {exc.code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
