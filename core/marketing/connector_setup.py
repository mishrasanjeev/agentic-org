"""CMO marketing connector setup readiness projection.

This module turns existing per-tenant ConnectorConfig rows into the
operator-facing checklist the CMO dashboard needs. It does not call vendor
APIs, decrypt credentials, or mark a connector healthy from registry metadata.
Only stored setup, health, scope, and sync facts can make a row ready.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

STALE_SYNC_AFTER = timedelta(hours=48)

CONFIGURED_STATUSES = {"active", "configured", "ready"}
UNCONFIGURED_STATUSES = {"deleted", "disabled", "inactive", "unconfigured"}
HEALTHY_STATUSES = {"healthy", "ok", "ready"}
DEGRADED_STATUSES = {"degraded", "warning", "partial", "rate_limited"}
EXPIRED_AUTH_MARKERS = ("expired", "invalid_grant", "reauthorize", "re-authorize", "401")
INSUFFICIENT_SCOPE_MARKERS = ("insufficient_scope", "missing_scope", "forbidden", "403")
MALFORMED_PAYLOAD_MARKERS = ("malformed_payload", "malformed payload", "invalid_payload", "schema_error")
QUOTA_EXHAUSTED_MARKERS = ("quota_exhausted", "quota exhausted", "quota", "limit exhausted")
CONNECTOR_DISABLED_MARKERS = ("connector_disabled", "connector disabled", "disabled", "inactive")
HUBSPOT_PRIVATE_APP_SCOPE_GAPS = {
    "crm.objects.contacts.read",
    "crm.objects.deals.read",
    "automation",
}


@dataclass(frozen=True)
class MarketingConnectorRequirement:
    key: str
    name: str
    category: str
    required_scopes: tuple[str, ...] = ()
    required_credentials: tuple[str, ...] = ()


MARKETING_CONNECTOR_REQUIREMENTS: tuple[MarketingConnectorRequirement, ...] = (
    MarketingConnectorRequirement(
        key="hubspot",
        name="HubSpot",
        category="CRM",
        required_scopes=("crm.objects.contacts.read", "crm.objects.deals.read", "automation"),
        required_credentials=("client_id", "client_secret", "refresh_token"),
    ),
    MarketingConnectorRequirement(
        key="salesforce",
        name="Salesforce",
        category="CRM",
        required_scopes=("api", "refresh_token", "offline_access"),
        required_credentials=("client_id", "client_secret", "refresh_token", "instance_url"),
    ),
    MarketingConnectorRequirement(
        key="google_ads",
        name="Google Ads",
        category="Ads",
        required_scopes=("https://www.googleapis.com/auth/adwords",),
        required_credentials=("client_id", "client_secret", "refresh_token", "developer_token", "customer_id"),
    ),
    MarketingConnectorRequirement(
        key="meta_ads",
        name="Meta Ads",
        category="Ads",
        required_scopes=("ads_read", "business_management"),
        required_credentials=("access_token", "ad_account_id"),
    ),
    MarketingConnectorRequirement(
        key="linkedin_ads",
        name="LinkedIn Ads",
        category="Ads",
        required_scopes=("r_ads_reporting", "rw_ads"),
        required_credentials=("client_id", "client_secret", "refresh_token", "account_id"),
    ),
    MarketingConnectorRequirement(
        key="ga4",
        name="Google Analytics 4",
        category="Analytics",
        required_scopes=("https://www.googleapis.com/auth/analytics.readonly",),
        required_credentials=("client_id", "client_secret", "refresh_token", "property_id"),
    ),
    MarketingConnectorRequirement(
        key="mixpanel",
        name="Mixpanel",
        category="Analytics",
        required_credentials=("project_id", "service_account_username", "service_account_secret"),
    ),
    MarketingConnectorRequirement(
        key="wordpress",
        name="WordPress",
        category="CMS",
        required_credentials=("site_url", "username", "application_password"),
    ),
    MarketingConnectorRequirement(
        key="mailchimp",
        name="Mailchimp",
        category="Email",
        required_credentials=("api_key", "server_prefix", "audience_id"),
    ),
    MarketingConnectorRequirement(
        key="sendgrid",
        name="SendGrid",
        category="Email",
        required_credentials=("api_key", "sender_identity"),
    ),
    MarketingConnectorRequirement(
        key="buffer",
        name="Buffer",
        category="Social",
        required_scopes=("profile:read", "updates:create"),
        required_credentials=("client_id", "client_secret", "refresh_token"),
    ),
    MarketingConnectorRequirement(
        key="twitter",
        name="X / Twitter",
        category="Social",
        required_scopes=("tweet.read", "tweet.write", "users.read", "offline.access"),
        required_credentials=("client_id", "client_secret", "refresh_token"),
    ),
    MarketingConnectorRequirement(
        key="youtube",
        name="YouTube",
        category="Social",
        required_scopes=("https://www.googleapis.com/auth/youtube.readonly",),
        required_credentials=("client_id", "client_secret", "refresh_token", "channel_id"),
    ),
    MarketingConnectorRequirement(
        key="ahrefs",
        name="Ahrefs",
        category="SEO",
        required_credentials=("api_token", "site_url"),
    ),
    MarketingConnectorRequirement(
        key="brandwatch",
        name="Brandwatch",
        category="Brand",
        required_scopes=("read_mentions", "read_queries"),
        required_credentials=("client_id", "client_secret", "refresh_token", "project_id"),
    ),
    MarketingConnectorRequirement(
        key="bombora",
        name="Bombora",
        category="ABM",
        required_credentials=("api_key", "company_id"),
    ),
    MarketingConnectorRequirement(
        key="g2",
        name="G2",
        category="ABM",
        required_credentials=("api_key", "vendor_id"),
    ),
    MarketingConnectorRequirement(
        key="trustradius",
        name="TrustRadius",
        category="ABM",
        required_credentials=("api_key", "vendor_id"),
    ),
    MarketingConnectorRequirement(
        key="stripe",
        name="Stripe",
        category="Finance",
        required_credentials=("api_key", "account_id"),
    ),
    MarketingConnectorRequirement(
        key="tally",
        name="Tally",
        category="Finance",
        required_credentials=("company_name", "bridge_url"),
    ),
)


def marketing_connector_keys() -> list[str]:
    return [requirement.key for requirement in MARKETING_CONNECTOR_REQUIREMENTS]


def build_marketing_connector_setup(
    connector_configs: Iterable[Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build CMO setup rows from existing ConnectorConfig-like objects."""

    now = _ensure_aware(now or datetime.now(UTC))
    configs_by_name = {
        str(getattr(config, "connector_name", "") or "").strip().lower(): config
        for config in connector_configs
    }

    rows: list[dict[str, Any]] = []
    for requirement in MARKETING_CONNECTOR_REQUIREMENTS:
        config = configs_by_name.get(requirement.key)
        rows.append(_build_row(requirement, config, now))
    return rows


def summarize_marketing_connector_setup(rows: Iterable[dict[str, Any]]) -> dict[str, int | bool | str]:
    items = list(rows)
    missing = sum(1 for row in items if row.get("health_status") == "missing")
    healthy = sum(1 for row in items if row.get("health_status") == "healthy")
    stale = sum(1 for row in items if row.get("health_status") == "stale")
    degraded = sum(1 for row in items if row.get("health_status") == "degraded")
    auth_actions = sum(
        1
        for row in items
        if row.get("health_status") in {"expired_auth", "insufficient_scope"}
    )
    needs_action = sum(1 for row in items if row.get("cta_state") != "none")
    return {
        "total": len(items),
        "healthy": healthy,
        "missing": missing,
        "stale": stale,
        "degraded": degraded,
        "auth_actions": auth_actions,
        "needs_action": needs_action,
        "readiness": "ready" if needs_action == 0 and items else "setup_required",
    }


def _build_row(
    requirement: MarketingConnectorRequirement,
    config: Any | None,
    now: datetime,
) -> dict[str, Any]:
    if config is None:
        return _missing_row(requirement)

    config_payload = _dict_or_empty(getattr(config, "config", None))
    status = str(getattr(config, "status", "") or "").strip().lower()
    health_status_raw = str(getattr(config, "health_status", "") or "").strip().lower()
    sync_error = str(getattr(config, "sync_error", "") or "")
    last_sync_at = _ensure_aware(getattr(config, "last_sync_at", None))
    credentials = getattr(config, "credentials_encrypted", None)
    has_credentials = bool(credentials)
    granted_scopes = _scopes_from_config(config_payload)
    missing_scopes = _missing_scopes(requirement.required_scopes, granted_scopes)
    contract_payload = _contract_payload(config_payload)
    contract_state = _contract_state_from_payload(contract_payload, last_sync_at, now)
    non_blocking_scope_gaps: list[str] = []

    if status in UNCONFIGURED_STATUSES or not has_credentials:
        return _missing_row(requirement)

    configured_status = "configured" if status in CONFIGURED_STATUSES or status else status
    status_text = " ".join(
        str(part or "")
        for part in (health_status_raw, sync_error, config_payload.get("auth_state"))
    ).lower()

    health_status = "degraded"
    cta_state = "review"
    data_coverage_status = "partial"
    detail = sync_error or "Connector configured but no healthy sync has been proven yet."

    if (
        contract_state == "missing_scope"
        or _contains_any(status_text, INSUFFICIENT_SCOPE_MARKERS)
    ) and not missing_scopes:
        missing_scopes = list(requirement.required_scopes)

    if _hubspot_private_app_scope_gap_is_non_blocking(
        requirement=requirement,
        missing_scopes=missing_scopes,
        status=status,
        health_status_raw=health_status_raw,
        status_text=status_text,
        contract_payload=contract_payload,
        contract_state=contract_state,
    ):
        non_blocking_scope_gaps = list(missing_scopes)
        missing_scopes = []

    if contract_state == "missing_scope" or _contains_any(status_text, INSUFFICIENT_SCOPE_MARKERS) or missing_scopes:
        health_status = "insufficient_scope"
        cta_state = "add_scope"
        data_coverage_status = "blocked"
        detail = "Connector is configured but required marketing scopes are missing."
    elif contract_state == "auth_expired" or _contains_any(status_text, EXPIRED_AUTH_MARKERS) or health_status_raw in {
        "expired_auth",
        "auth_expired",
        "token_expired",
    }:
        health_status = "expired_auth"
        cta_state = "reconnect"
        data_coverage_status = "blocked"
        detail = sync_error or "Connector auth is expired and must be reauthorized."
    elif contract_state == "connector_disabled" or _contains_any(status_text, CONNECTOR_DISABLED_MARKERS):
        health_status = "degraded"
        cta_state = "enable_connector"
        data_coverage_status = "blocked"
        detail = _contract_degraded_reason(contract_payload, "connector_disabled")
    elif contract_state == "malformed_payload" or _contains_any(status_text, MALFORMED_PAYLOAD_MARKERS):
        health_status = "degraded"
        cta_state = "fix_connector_payload"
        data_coverage_status = "blocked"
        detail = _contract_degraded_reason(contract_payload, "malformed_payload")
    elif contract_state == "quota_exhausted" or _contains_any(status_text, QUOTA_EXHAUSTED_MARKERS):
        health_status = "degraded"
        cta_state = "wait_for_quota_reset"
        data_coverage_status = "partial"
        detail = _contract_degraded_reason(contract_payload, "quota_exhausted")
    elif contract_state in {"rate_limited", "timeout", "vendor_5xx", "partial_data", "degraded"}:
        health_status = "degraded"
        cta_state = "review"
        data_coverage_status = "partial"
        detail = _contract_degraded_reason(contract_payload, contract_state)
    elif contract_state == "stale_data":
        health_status = "stale"
        cta_state = "refresh"
        data_coverage_status = "stale"
        detail = _contract_degraded_reason(contract_payload, contract_state)
    elif last_sync_at is None:
        health_status = "degraded"
        cta_state = "refresh"
        data_coverage_status = "missing"
        detail = "Connector is configured but has no successful sync timestamp yet."
    elif last_sync_at is not None and now - last_sync_at > STALE_SYNC_AFTER:
        health_status = "stale"
        cta_state = "refresh"
        data_coverage_status = "stale"
        detail = "Last successful sync is older than the CMO freshness threshold."
    elif health_status_raw in HEALTHY_STATUSES:
        health_status = "healthy"
        cta_state = "none"
        data_coverage_status = _coverage_from_config(config_payload, "ready")
        detail = "Configured, healthy, and recently synced."
    elif health_status_raw in DEGRADED_STATUSES:
        health_status = "degraded"
        cta_state = "review"
        data_coverage_status = _coverage_from_config(config_payload, "partial")
        detail = sync_error or "Connector is degraded; marketing data may be incomplete."

    return {
        "key": requirement.key,
        "name": str(getattr(config, "display_name", None) or requirement.name),
        "category": requirement.category,
        "required_scopes": list(requirement.required_scopes),
        "required_credentials": list(requirement.required_credentials),
        "configured_status": configured_status,
        "health_status": health_status,
        "last_sync_at": _isoformat(last_sync_at),
        "owner": _owner_from_config(config_payload),
        "account_id": _account_id_from_config(config_payload),
        "data_coverage_status": data_coverage_status,
        "cta_state": cta_state,
        "missing_scopes": missing_scopes,
        "non_blocking_scope_gaps": non_blocking_scope_gaps,
        "granted_scopes": granted_scopes,
        "detail": detail,
    }


def _missing_row(requirement: MarketingConnectorRequirement) -> dict[str, Any]:
    return {
        "key": requirement.key,
        "name": requirement.name,
        "category": requirement.category,
        "required_scopes": list(requirement.required_scopes),
        "required_credentials": list(requirement.required_credentials),
        "configured_status": "unconfigured",
        "health_status": "missing",
        "last_sync_at": None,
        "owner": "Unassigned",
        "account_id": None,
        "data_coverage_status": "missing",
        "cta_state": "setup",
        "missing_scopes": list(requirement.required_scopes),
        "non_blocking_scope_gaps": [],
        "granted_scopes": [],
        "detail": "Connector is missing; configure it before treating CMO data as production-ready.",
    }


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _contract_payload(config: dict[str, Any]) -> dict[str, Any]:
    value = (
        config.get("marketing_connector_contract")
        or config.get("marketing_contract")
        or config.get("connector_contract")
        or {}
    )
    return value if isinstance(value, dict) else {}


def _contract_state_from_payload(
    payload: dict[str, Any],
    last_sync_at: datetime | None,
    now: datetime,
) -> str:
    raw = str(
        payload.get("state")
        or payload.get("contract_state")
        or payload.get("status")
        or payload.get("health_status")
        or ""
    ).strip().lower()
    normalized = raw.replace("-", "_").replace(" ", "_")
    status_text = " ".join(
        str(part or "")
        for part in (
            normalized,
            payload.get("degraded_mode_reason"),
            payload.get("blocking_reason"),
            payload.get("last_error"),
        )
    ).lower()
    ttl_seconds = _int_or_none(payload.get("ttl_seconds") or payload.get("data_ttl_seconds"))
    contract_sync = _parse_datetime(payload.get("last_sync_at") or payload.get("last_successful_sync_at"))
    effective_sync = contract_sync or last_sync_at
    if (
        normalized in {"", "healthy"}
        and ttl_seconds
        and effective_sync
        and now - effective_sync > timedelta(seconds=ttl_seconds)
    ):
        return "stale_data"
    if _contains_any(status_text, CONNECTOR_DISABLED_MARKERS):
        return "connector_disabled"
    if _contains_any(status_text, MALFORMED_PAYLOAD_MARKERS):
        return "malformed_payload"
    if _contains_any(status_text, QUOTA_EXHAUSTED_MARKERS):
        return "quota_exhausted"
    return normalized


def _contract_degraded_reason(payload: dict[str, Any], state: str) -> str:
    explicit = str(
        payload.get("degraded_mode_reason")
        or payload.get("blocking_reason")
        or payload.get("last_error")
        or ""
    ).strip()
    if explicit:
        return explicit
    return {
        "rate_limited": "Connector is rate limited; retry budget controls when it can be retried.",
        "timeout": "Connector timed out; use degraded mode until retry succeeds.",
        "vendor_5xx": "Vendor returned a 5xx error; use degraded mode until vendor recovers.",
        "partial_data": "Connector returned partial data; KPI confidence must be downgraded.",
        "stale_data": "Connector data is stale beyond its TTL.",
        "malformed_payload": "Connector returned malformed data; fix the connector payload before production use.",
        "quota_exhausted": "Connector quota is exhausted; retry only after the reset window.",
        "connector_disabled": "Connector is disabled and blocks dependent CMO workflows.",
        "degraded": "Connector contract is degraded; marketing data may be incomplete.",
    }.get(state, "Connector contract is degraded; marketing data may be incomplete.")


def _coverage_from_config(config: dict[str, Any], fallback: str) -> str:
    value = str(config.get("data_coverage_status") or config.get("data_coverage") or "").strip().lower()
    return value if value in {"ready", "missing", "blocked", "stale", "partial", "unavailable"} else fallback


def _owner_from_config(config: dict[str, Any]) -> str:
    for key in ("owner", "owner_email", "admin_email", "marketing_owner"):
        value = str(config.get(key) or "").strip()
        if value:
            return value
    return "Unassigned"


def _account_id_from_config(config: dict[str, Any]) -> str | None:
    for key in (
        "account_id",
        "workspace_id",
        "customer_id",
        "ad_account_id",
        "property_id",
        "site_url",
        "organization_id",
        "project_id",
        "vendor_id",
        "channel_id",
        "company_name",
    ):
        value = str(config.get(key) or "").strip()
        if value:
            return value
    return None


def _scopes_from_config(config: dict[str, Any]) -> list[str]:
    raw = (
        config.get("granted_scopes")
        or config.get("validated_scopes")
        or config.get("scopes")
        or config.get("scope")
    )
    if isinstance(raw, str):
        return [part for part in raw.replace(",", " ").split() if part]
    if isinstance(raw, (list, tuple, set)):
        return [str(part) for part in raw if str(part).strip()]
    return []


def _missing_scopes(required: tuple[str, ...], granted: list[str]) -> list[str]:
    if not required or not granted:
        return []
    granted_set = set(granted)
    return [scope for scope in required if scope not in granted_set]


def _hubspot_private_app_scope_gap_is_non_blocking(
    *,
    requirement: MarketingConnectorRequirement,
    missing_scopes: list[str],
    status: str,
    health_status_raw: str,
    status_text: str,
    contract_payload: dict[str, Any],
    contract_state: str,
) -> bool:
    """Treat healthy HubSpot CRM access as setup proof despite scope text gaps."""

    if requirement.key != "hubspot" or not missing_scopes:
        return False
    if not set(missing_scopes).issubset(HUBSPOT_PRIVATE_APP_SCOPE_GAPS):
        return False
    if status not in CONFIGURED_STATUSES and status:
        return False
    if health_status_raw not in HEALTHY_STATUSES:
        return False
    explicit_scope_failure = " ".join(
        str(part or "")
        for part in (
            status_text,
            contract_payload.get("degraded_mode_reason"),
            contract_payload.get("blocking_reason"),
            contract_payload.get("last_error"),
        )
    ).lower()
    if contract_state == "missing_scope" or _contains_any(explicit_scope_failure, INSUFFICIENT_SCOPE_MARKERS):
        return False
    return True


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _ensure_aware(parsed)
    return None


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
