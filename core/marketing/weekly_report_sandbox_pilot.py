"""CMO-PROD-3 sandbox walk-through orchestration.

This module orchestrates a fail-closed sandbox-pilot run for the weekly
marketing report. It does **not** invent data: when sandbox credentials or
config are missing, it returns a structured `blocked` envelope listing
exactly which env vars / DB / migration / connector configs / mappings
are absent, so the caller can populate them. When everything is present,
it delegates to the existing CMO-PROD-2 persistence helper through the
real report-task path, so the same code that runs in production runs in
the sandbox.

Critically:

* This module never marks a verdict as ``passed`` for ``real_vendor``.
* It refuses to insert any proof row when preflight fails.
* All log / output dictionaries are redacted via the same key-marker
  redactor used by ``core.marketing.weekly_report_pilot_proof``.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.marketing.weekly_report_pilot_proof import SECRET_KEY_MARKERS

logger = logging.getLogger(__name__)


CMO_PROD_3_VERSION = "2026-05-24.cmo-prod-3"


SANDBOX_ENV_PREFIX = "AGENTICORG_CMO_SANDBOX_"
CATEGORY_ORDER = ("CRM", "Ads", "Analytics", "Email")
READY_STATUSES = {"active", "configured", "connected", "healthy", "ok", "ready"}
READY_HEALTH = {"", "active", "configured", "connected", "healthy", "ok", "ready", "unknown"}
VENDOR_SANDBOX_SCOPES = {
    "qa_sandbox",
    "sandbox",
    "vendor-sandbox",
    "vendor_sandbox",
    "vendor_sandbox_proof",
}
LOCAL_PREFLIGHT_SCOPES = {"local_preflight_only", "preflight_only"}
TRUTHY = {"1", "true", "yes", "y", "on"}
CATEGORY_ALIASES = {
    "CRM": {"crm", "hubspot", "hubspot_crm", "salesforce", "salesforce_crm"},
    "Ads": {"ad", "ads", "google_ads", "linkedin_ads", "meta_ads"},
    "Analytics": {"analytics", "ga4", "google_analytics", "google_analytics_4", "gsc"},
    "Email": {"email", "mailchimp", "sendgrid", "hubspot_email"},
}


# Per category, the env-var groups that constitute a usable sandbox
# connector. The runner picks the first group whose required env vars
# are all populated. A category is *missing* only if no group can be
# satisfied.
SANDBOX_CONNECTOR_OPTIONS: dict[str, tuple[dict[str, Any], ...]] = {
    "CRM": (
        {
            "connector_key": "hubspot",
            "required_envs": (f"{SANDBOX_ENV_PREFIX}HUBSPOT_ACCESS_TOKEN",),
            "credential_env_map": {"access_token": f"{SANDBOX_ENV_PREFIX}HUBSPOT_ACCESS_TOKEN"},
        },
        {
            "connector_key": "salesforce",
            "required_envs": (
                f"{SANDBOX_ENV_PREFIX}SALESFORCE_INSTANCE_URL",
                f"{SANDBOX_ENV_PREFIX}SALESFORCE_REFRESH_TOKEN",
                f"{SANDBOX_ENV_PREFIX}SALESFORCE_CLIENT_ID",
                f"{SANDBOX_ENV_PREFIX}SALESFORCE_CLIENT_SECRET",
            ),
            "credential_env_map": {
                "instance_url": f"{SANDBOX_ENV_PREFIX}SALESFORCE_INSTANCE_URL",
                "refresh_token": f"{SANDBOX_ENV_PREFIX}SALESFORCE_REFRESH_TOKEN",
                "client_id": f"{SANDBOX_ENV_PREFIX}SALESFORCE_CLIENT_ID",
                "client_secret": f"{SANDBOX_ENV_PREFIX}SALESFORCE_CLIENT_SECRET",
            },
        },
    ),
    "Ads": (
        {
            "connector_key": "google_ads",
            "required_envs": (
                f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_DEVELOPER_TOKEN",
                f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_REFRESH_TOKEN",
                f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_CUSTOMER_ID",
                f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_CLIENT_ID",
                f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_CLIENT_SECRET",
            ),
            "credential_env_map": {
                "developer_token": f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_DEVELOPER_TOKEN",
                "refresh_token": f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_REFRESH_TOKEN",
                "customer_id": f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_CUSTOMER_ID",
                "client_id": f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_CLIENT_ID",
                "client_secret": f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_CLIENT_SECRET",
            },
        },
        {
            "connector_key": "meta_ads",
            "required_envs": (
                f"{SANDBOX_ENV_PREFIX}META_ADS_ACCESS_TOKEN",
                f"{SANDBOX_ENV_PREFIX}META_ADS_AD_ACCOUNT_ID",
            ),
            "credential_env_map": {
                "access_token": f"{SANDBOX_ENV_PREFIX}META_ADS_ACCESS_TOKEN",
                "ad_account_id": f"{SANDBOX_ENV_PREFIX}META_ADS_AD_ACCOUNT_ID",
            },
        },
        {
            "connector_key": "linkedin_ads",
            "required_envs": (
                f"{SANDBOX_ENV_PREFIX}LINKEDIN_ADS_REFRESH_TOKEN",
                f"{SANDBOX_ENV_PREFIX}LINKEDIN_ADS_ACCOUNT_ID",
                f"{SANDBOX_ENV_PREFIX}LINKEDIN_ADS_CLIENT_ID",
                f"{SANDBOX_ENV_PREFIX}LINKEDIN_ADS_CLIENT_SECRET",
            ),
            "credential_env_map": {
                "refresh_token": f"{SANDBOX_ENV_PREFIX}LINKEDIN_ADS_REFRESH_TOKEN",
                "account_id": f"{SANDBOX_ENV_PREFIX}LINKEDIN_ADS_ACCOUNT_ID",
                "client_id": f"{SANDBOX_ENV_PREFIX}LINKEDIN_ADS_CLIENT_ID",
                "client_secret": f"{SANDBOX_ENV_PREFIX}LINKEDIN_ADS_CLIENT_SECRET",
            },
        },
    ),
    "Analytics": (
        {
            "connector_key": "ga4",
            "required_envs": (
                f"{SANDBOX_ENV_PREFIX}GA4_PROPERTY_ID",
                f"{SANDBOX_ENV_PREFIX}GA4_REFRESH_TOKEN",
                f"{SANDBOX_ENV_PREFIX}GA4_CLIENT_ID",
                f"{SANDBOX_ENV_PREFIX}GA4_CLIENT_SECRET",
            ),
            "credential_env_map": {
                "property_id": f"{SANDBOX_ENV_PREFIX}GA4_PROPERTY_ID",
                "refresh_token": f"{SANDBOX_ENV_PREFIX}GA4_REFRESH_TOKEN",
                "client_id": f"{SANDBOX_ENV_PREFIX}GA4_CLIENT_ID",
                "client_secret": f"{SANDBOX_ENV_PREFIX}GA4_CLIENT_SECRET",
            },
        },
    ),
    "Email": (
        {
            "connector_key": "sendgrid",
            "required_envs": (
                f"{SANDBOX_ENV_PREFIX}SENDGRID_API_KEY",
                f"{SANDBOX_ENV_PREFIX}SENDGRID_SENDER",
            ),
            "credential_env_map": {
                "api_key": f"{SANDBOX_ENV_PREFIX}SENDGRID_API_KEY",
                "sender_identity": f"{SANDBOX_ENV_PREFIX}SENDGRID_SENDER",
            },
        },
        {
            "connector_key": "mailchimp",
            "required_envs": (
                f"{SANDBOX_ENV_PREFIX}MAILCHIMP_API_KEY",
                f"{SANDBOX_ENV_PREFIX}MAILCHIMP_SERVER_PREFIX",
                f"{SANDBOX_ENV_PREFIX}MAILCHIMP_AUDIENCE_ID",
            ),
            "credential_env_map": {
                "api_key": f"{SANDBOX_ENV_PREFIX}MAILCHIMP_API_KEY",
                "server_prefix": f"{SANDBOX_ENV_PREFIX}MAILCHIMP_SERVER_PREFIX",
                "audience_id": f"{SANDBOX_ENV_PREFIX}MAILCHIMP_AUDIENCE_ID",
            },
        },
    ),
}

REQUIRED_BASE_ENVS: tuple[str, ...] = (
    "AGENTICORG_DB_URL",
    f"{SANDBOX_ENV_PREFIX}TENANT_ID",
)
OPTIONAL_BASE_ENVS: tuple[str, ...] = (f"{SANDBOX_ENV_PREFIX}COMPANY_ID",)


@dataclass
class SandboxPilotConfig:
    db_url: str | None = None
    tenant_id: str | None = None
    company_id: str | None = None
    chosen_connectors: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing_envs: list[str] = field(default_factory=list)
    missing_categories: list[str] = field(default_factory=list)
    db_discovery_state: str = "not_checked"
    local_preflight_only: bool = False


@dataclass
class SandboxPilotPreflight:
    preflight_status: str  # "ready" or "blocked"
    config: SandboxPilotConfig
    blockers: list[dict[str, Any]]
    next_actions: list[dict[str, Any]]
    proof_status: str = "unavailable"


def discover_sandbox_pilot_config(
    env: Mapping[str, str] | None = None,
    *,
    connector_rows: Sequence[Any] | None = None,
) -> SandboxPilotPreflight:
    """Inspect tenant ConnectorConfig rows/env vars and return a preflight verdict.

    Tenant-scoped ConnectorConfig rows are the preferred source because that
    matches the product connector setup path. Env vars remain a local/dev
    fallback for missing categories or DB-discovery outages. The function never
    returns credential values.
    """

    env = env if env is not None else os.environ
    blockers: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []
    config = SandboxPilotConfig()

    db_url = env.get("AGENTICORG_DB_URL") or None
    config.db_url = db_url
    if not db_url:
        blockers.append(
            {
                "category": "database",
                "missing_env": "AGENTICORG_DB_URL",
                "severity": "critical",
                "message": "DB URL is not configured; cannot run migration or persist sandbox proof.",
                "next_action": "configure_db_url",
            }
        )
        next_actions.append(
            {
                "action_key": "configure_db_url",
                "label": "Set AGENTICORG_DB_URL to a Postgres URL with the v4917 migration applied.",
            }
        )

    tenant_id = env.get(f"{SANDBOX_ENV_PREFIX}TENANT_ID") or None
    config.tenant_id = tenant_id
    if not tenant_id:
        blockers.append(
            {
                "category": "tenant",
                "missing_env": f"{SANDBOX_ENV_PREFIX}TENANT_ID",
                "severity": "critical",
                "message": "Sandbox tenant id is not configured.",
                "next_action": "configure_sandbox_tenant",
            }
        )
        next_actions.append(
            {
                "action_key": "configure_sandbox_tenant",
                "label": (
                    f"Create or pick a sandbox tenant and export its UUID as "
                    f"{SANDBOX_ENV_PREFIX}TENANT_ID."
                ),
            }
        )
    else:
        try:
            uuid.UUID(tenant_id)
        except ValueError:
            blockers.append(
                {
                    "category": "tenant",
                    "severity": "critical",
                    "message": f"{SANDBOX_ENV_PREFIX}TENANT_ID is not a valid UUID.",
                    "next_action": "configure_sandbox_tenant",
                }
            )

    company_id = env.get(f"{SANDBOX_ENV_PREFIX}COMPANY_ID") or None
    config.company_id = company_id

    db_result = _discover_connector_config_categories(
        env=env,
        tenant_id=tenant_id,
        company_id=company_id,
        db_url=db_url,
        connector_rows=connector_rows,
    )
    config.db_discovery_state = db_result["state"]
    db_categories: dict[str, dict[str, Any]] = db_result["categories"]
    if db_result["state"] == "blocked":
        blockers.append(
            {
                "category": "connector_config",
                "severity": "critical",
                "message": db_result["message"],
                "next_action": "configure_sandbox_tenant",
            }
        )

    for category, options in SANDBOX_CONNECTOR_OPTIONS.items():
        db_choice = db_categories.get(category)
        if db_choice is not None:
            config.chosen_connectors[category] = db_choice
            if db_choice.get("local_preflight_only"):
                config.local_preflight_only = True
            if db_choice.get("readiness_state") != "ready":
                config.missing_categories.append(category)
                blockers.append(
                    {
                        "category": "connector",
                        "connector_category": category,
                        "severity": "critical",
                        "message": str(
                            db_choice.get("missing_reason")
                            or f"Tenant ConnectorConfig for {category} is not usable."
                        ),
                        "next_action": f"configure_{category.lower()}_sandbox_connector",
                    }
                )
                next_actions.append(
                    {
                        "action_key": f"configure_{category.lower()}_sandbox_connector",
                        "label": (
                            f"Update the tenant ConnectorConfig for {category} to a real "
                            "vendor-sandbox connector with usable status/health and "
                            "proof_scope=vendor_sandbox."
                        ),
                    }
                )
            continue

        chosen: dict[str, Any] | None = None
        category_missing_envs: list[str] = []
        for option in options:
            envs = option["required_envs"]
            missing = [name for name in envs if not env.get(name)]
            if not missing:
                chosen = {
                    "connector_key": option["connector_key"],
                    "connector_name": option["connector_key"],
                    "source": "env",
                    "readiness_state": "ready",
                    "proof_scope": "local_env_fallback",
                    "local_test_only": False,
                    "credential_envs": dict(option["credential_env_map"]),
                }
                break
            # Track first option's missing envs so we can suggest exactly
            # which set of vars to populate.
            if not category_missing_envs:
                category_missing_envs = missing
        if chosen is None:
            config.missing_categories.append(category)
            for env_name in category_missing_envs:
                if env_name not in config.missing_envs:
                    config.missing_envs.append(env_name)
            blockers.append(
                {
                    "category": "connector",
                    "connector_category": category,
                    "severity": "critical",
                    "missing_envs": category_missing_envs,
                    "message": (
                        f"No sandbox connector option for category {category} is fully "
                        f"configured. Populate one of: "
                        f"{', '.join(opt['connector_key'] for opt in options)}."
                    ),
                    "next_action": f"configure_{category.lower()}_sandbox_connector",
                }
            )
            next_actions.append(
                {
                    "action_key": f"configure_{category.lower()}_sandbox_connector",
                    "label": (
                        f"Pick one {category} sandbox connector and export the env vars "
                        f"listed in SANDBOX_CONNECTOR_OPTIONS[{category!r}]."
                    ),
                }
            )
        else:
            config.chosen_connectors[category] = chosen

    status = "ready" if not blockers else "blocked"
    proof_status = "unavailable"
    if status == "ready":
        proof_status = "local_preflight_only" if config.local_preflight_only else "preflight_ready"
    return SandboxPilotPreflight(
        preflight_status=status,
        config=config,
        blockers=blockers,
        next_actions=next_actions,
        proof_status=proof_status,
    )


def build_blocked_preflight_envelope(preflight: SandboxPilotPreflight) -> dict[str, Any]:
    """Return a stable, redacted envelope describing why preflight failed.

    The envelope intentionally mirrors the persisted-proof summary shape
    used by ``/kpis/cmo`` so dashboards can render either an actual
    ``weekly_report_pilot_proof_summary`` row or this preflight result
    with the same component.
    """

    return _redact(
        {
            "schema_version": CMO_PROD_3_VERSION,
            "preflight_status": preflight.preflight_status,
            "environment_type": "vendor_sandbox",
            "proof_status": preflight.proof_status,
            "production_claim_allowed": False,
            "real_vendor_claim_allowed": False,
            "tenant_id": preflight.config.tenant_id,
            "company_id": preflight.config.company_id,
            "db_discovery_state": preflight.config.db_discovery_state,
            "chosen_connectors": preflight.config.chosen_connectors,
            "missing_envs": preflight.config.missing_envs,
            "missing_categories": preflight.config.missing_categories,
            "blockers": preflight.blockers,
            "next_actions": preflight.next_actions,
            "generated_at": datetime.now(UTC).isoformat(),
            "note": (
                "CMO-PROD-3 cannot synthesize sandbox proof. No row will be inserted "
                "into weekly_report_pilot_proofs while preflight is blocked. Resolve "
                "the listed env vars / DB / migration prerequisites and re-run."
            ),
        }
    )


async def run_sandbox_pilot(
    *,
    env: Mapping[str, str] | None = None,
    connector_rows: Sequence[Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute the sandbox walk-through when preflight is ready.

    Drives the real weekly-report path:

      1. Resolve config from env vars (no fixtures).
      2. If preflight fails, return the blocked envelope unchanged.
      3. Otherwise, call the real ``generate_report`` Celery task entry
         point. The CMO-PROD-2 hook persists the verdict via the same
         path production uses.
      4. Read the latest persisted verdict back so the caller sees what
         landed in the DB (proof_id, proof_status, blockers count).

    Returns a redacted summary dict.
    """

    preflight = discover_sandbox_pilot_config(env, connector_rows=connector_rows)
    if preflight.preflight_status != "ready":
        return build_blocked_preflight_envelope(preflight)
    if preflight.proof_status == "local_preflight_only":
        return _redact(
            {
                "schema_version": CMO_PROD_3_VERSION,
                "preflight_status": "ready",
                "environment_type": "vendor_sandbox",
                "proof_status": "local_preflight_only",
                "production_claim_allowed": False,
                "real_vendor_claim_allowed": False,
                "proof_inserted": False,
                "tenant_id": preflight.config.tenant_id,
                "company_id": preflight.config.company_id,
                "db_discovery_state": preflight.config.db_discovery_state,
                "chosen_connectors": preflight.config.chosen_connectors,
                "generated_at": (now or datetime.now(UTC)).isoformat(),
                "note": (
                    "Tenant ConnectorConfig rows are local/preflight-only. This can "
                    "prove DB discovery but cannot create sandbox_proven or production proof."
                ),
            }
        )

    # Local imports keep test/runner light and avoid Celery/DB import in
    # the blocked path.
    from core.marketing.weekly_report_pilot_persistence import (
        latest_weekly_report_pilot_proof,
        summarize_persisted_proof,
    )
    from core.tasks import report_tasks

    config = preflight.config
    report_config = {
        "report_type": "cmo_weekly",
        "params": {"pilot_environment_type": "vendor_sandbox"},
        "tenant_id": str(config.tenant_id),
        "company_id": str(config.company_id) if config.company_id else "default",
        "format": "pdf",
        "delivery_channels": [],
    }
    task_result = report_tasks.generate_report.run(report_config)

    # Verify the verdict landed in DB by re-reading it.
    persisted_summary: dict[str, Any] | None = None
    try:
        from core.database import get_tenant_session

        async with get_tenant_session(uuid.UUID(str(config.tenant_id))) as session:
            row = await latest_weekly_report_pilot_proof(
                session,
                tenant_id=str(config.tenant_id),
                company_id=str(config.company_id) if config.company_id else None,
            )
        persisted_summary = summarize_persisted_proof(row)
    # enterprise-gate: broad-except-ok reason=sandbox-runner-tolerates-db-readback-failure
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sandbox_pilot_db_readback_failed",
            extra={"error": str(exc)},
        )

    summary_from_task = (
        task_result.get("weekly_report_pilot_proof") if isinstance(task_result, dict) else None
    )
    return _redact(
        {
            "schema_version": CMO_PROD_3_VERSION,
            "preflight_status": "ready",
            "environment_type": "vendor_sandbox",
            "tenant_id": config.tenant_id,
            "company_id": config.company_id,
            "report_task_result": _strip_paths(task_result),
            "weekly_report_pilot_proof": summary_from_task,
            "latest_weekly_report_pilot_proof": persisted_summary,
            "generated_at": (now or datetime.now(UTC)).isoformat(),
        }
    )


def _strip_paths(result: Any) -> Any:
    """Drop filesystem paths from a task result to keep the runbook output safe."""

    if not isinstance(result, Mapping):
        return result
    stripped = dict(result)
    paths = stripped.get("paths")
    if isinstance(paths, list):
        stripped["paths"] = [
            f"<{len(paths)} report artifact(s) written>" if paths else "<no artifacts>"
        ]
    return stripped


def _discover_connector_config_categories(
    *,
    env: Mapping[str, str],
    tenant_id: str | None,
    company_id: str | None,
    db_url: str | None,
    connector_rows: Sequence[Any] | None,
) -> dict[str, Any]:
    if not tenant_id:
        return {"state": "not_checked", "message": "Sandbox tenant id is missing.", "categories": {}}
    try:
        parsed_tenant_id = uuid.UUID(str(tenant_id))
    except ValueError:
        return {
            "state": "blocked",
            "message": f"{SANDBOX_ENV_PREFIX}TENANT_ID must be a valid UUID.",
            "categories": {},
        }

    try:
        parsed_company_id = uuid.UUID(str(company_id)) if company_id else None
    except ValueError:
        return {
            "state": "blocked",
            "message": f"{SANDBOX_ENV_PREFIX}COMPANY_ID must be a valid UUID.",
            "categories": {},
        }

    try:
        rows = list(connector_rows) if connector_rows is not None else _load_connector_config_rows(
            parsed_tenant_id,
            parsed_company_id,
            db_url,
        )
    # enterprise-gate: broad-except-ok reason=sandbox-preflight-must-fail-closed-on-db-discovery-errors
    except Exception:
        return {
            "state": "unavailable",
            "message": "Tenant ConnectorConfig discovery is unavailable; env fallback may be used.",
            "categories": {},
        }

    categories: dict[str, dict[str, Any]] = {}
    for row in rows:
        category = _category_for_connector_row(row)
        if category is None:
            continue
        entry = _safe_connector_entry(category, row)
        existing = categories.get(category)
        if existing is None or (entry["readiness_state"] == "ready" and existing["readiness_state"] != "ready"):
            categories[category] = entry
    return {"state": "ready", "message": "Tenant ConnectorConfig discovery completed.", "categories": categories}


def _load_connector_config_rows(
    tenant_id: uuid.UUID,
    company_id: uuid.UUID | None,
    db_url: str | None,
) -> list[dict[str, Any]]:
    if not db_url:
        return []
    from sqlalchemy import create_engine, text

    engine = create_engine(_sync_db_url(db_url), connect_args={"connect_timeout": 2}, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            company_scope = str(company_id) if company_id is not None else ""
            conn.execute(
                text("SELECT set_config('agenticorg.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            conn.execute(
                text("SELECT set_config('agenticorg.company_id', :company_id, true)"),
                {"company_id": company_scope},
            )
            result = conn.execute(
                text(
                    """
                    SELECT
                        connector_name,
                        display_name,
                        status,
                        health_status,
                        config,
                        credentials_encrypted
                    FROM connector_configs
                    WHERE tenant_id = CAST(:tenant_id AS UUID)
                      AND company_id IS NOT DISTINCT FROM
                          CAST(NULLIF(:company_id, '') AS UUID)
                    """
                ),
                {"tenant_id": str(tenant_id), "company_id": company_scope},
            )
            return [dict(row._mapping) for row in result.fetchall()]
    finally:
        engine.dispose()


def _safe_connector_entry(category: str, row: Any) -> dict[str, Any]:
    config = _dict_or_empty(_row_value(row, "config"))
    proof_scope = _clean_public_text(
        config.get("proof_scope")
        or config.get("environment_type")
        or config.get("environment")
        or config.get("source_context")
    )
    local_test_only = _truthy(config.get("local_test_only"))
    mock_or_test_double = _truthy(config.get("mock_or_test_double") or config.get("test_double") or config.get("mock"))
    status = str(_row_value(row, "status") or "").strip().lower()
    health = str(_row_value(row, "health_status") or "").strip().lower()

    missing_reason = None
    if status not in READY_STATUSES:
        missing_reason = f"ConnectorConfig status is not usable ({status or 'missing'})."
    elif health not in READY_HEALTH:
        missing_reason = f"ConnectorConfig health is not usable ({health or 'missing'})."
    elif mock_or_test_double:
        missing_reason = "ConnectorConfig is marked mock_or_test_double/test_double."
    elif local_test_only or _normalize_token(proof_scope) in LOCAL_PREFLIGHT_SCOPES:
        missing_reason = None
    elif _normalize_token(proof_scope) not in VENDOR_SANDBOX_SCOPES:
        missing_reason = "ConnectorConfig proof_scope/environment is not vendor_sandbox."

    local_preflight_only = bool(
        missing_reason is None
        and (local_test_only or _normalize_token(proof_scope) in LOCAL_PREFLIGHT_SCOPES)
    )
    return {
        "connector_key": _clean_public_text(_row_value(row, "connector_name")),
        "connector_name": _clean_public_text(_row_value(row, "display_name"))
        or _clean_public_text(_row_value(row, "connector_name")),
        "source": "db",
        "readiness_state": "blocked" if missing_reason else "ready",
        "missing_reason": missing_reason,
        "proof_scope": proof_scope,
        "local_test_only": local_test_only,
        "local_preflight_only": local_preflight_only,
        "mock_or_test_double": mock_or_test_double,
        "category": category,
    }


def _category_for_connector_row(row: Any) -> str | None:
    config = _dict_or_empty(_row_value(row, "config"))
    explicit = (
        config.get("cmo_category")
        or config.get("connector_category")
        or config.get("category")
        or config.get("source_domain")
    )
    category = _normalize_category(explicit)
    if category:
        return category
    return _category_from_name(str(_row_value(row, "connector_name") or ""))


def _normalize_category(value: Any) -> str | None:
    normalized = _normalize_token(value)
    for category, aliases in CATEGORY_ALIASES.items():
        if normalized == _normalize_token(category) or normalized in aliases:
            return category
    return None


def _category_from_name(value: str) -> str | None:
    normalized = _normalize_token(value)
    for category, aliases in CATEGORY_ALIASES.items():
        if normalized in aliases or any(alias in normalized for alias in aliases if len(alias) > 3):
            return category
    return None


def _sync_db_url(db_url: str) -> str:
    return db_url.replace("postgresql+asyncpg", "postgresql").replace("+asyncpg", "")


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUTHY


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _clean_public_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if any(marker in text.lower() for marker in SECRET_KEY_MARKERS):
        return "[REDACTED]"
    return text


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key).lower()
            if any(marker in text_key for marker in SECRET_KEY_MARKERS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple | set):
        return [_redact(item) for item in value]
    return value
