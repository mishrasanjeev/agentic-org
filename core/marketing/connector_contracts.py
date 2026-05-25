"""CMO marketing connector contract readiness projection.

This module turns stored ConnectorConfig metadata into a machine-checkable
connector contract for CMO agents and workflows. It separates read readiness
from write readiness, represents retry/idempotency facts, and requires explicit
external write confirmation before a write step can be considered complete.
It does not call vendor APIs or treat test doubles as production proof.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from core.marketing.connector_retry_policy import (
    build_degraded_mode_projection,
    normalize_failure_class,
    policy_projection,
    summarize_degraded_modes,
)
from core.marketing.connector_setup import (
    MARKETING_CONNECTOR_REQUIREMENTS,
    STALE_SYNC_AFTER,
)

CONTRACT_STATES = (
    "healthy",
    "missing_scope",
    "insufficient_scope",
    "auth_expired",
    "rate_limited",
    "timeout",
    "vendor_5xx",
    "partial_data",
    "stale_data",
    "malformed_payload",
    "quota_exhausted",
    "connector_disabled",
    "degraded",
    "write_unconfirmed",
    "write_confirmed",
)
WRITE_CONFIRMATION_STATES = (
    "accepted",
    "rejected",
    "timeout_unknown",
    "retry_scheduled",
    "idempotent_recovered",
    "write_confirmed",
    "write_unconfirmed",
    "draft_created",
    "shadow_only",
)

AUTH_EXPIRED_MARKERS = ("expired", "invalid_grant", "reauthorize", "re-authorize", "401")
MISSING_SCOPE_MARKERS = ("insufficient_scope", "missing_scope", "forbidden", "403")
RATE_LIMIT_MARKERS = ("rate_limited", "rate limit", "429")
TIMEOUT_MARKERS = ("timeout", "timed out", "deadline")
VENDOR_5XX_MARKERS = ("5xx", "500", "502", "503", "504", "server error")
PARTIAL_DATA_MARKERS = ("partial_data", "partial data", "partial")
STALE_DATA_MARKERS = ("stale_data", "stale data", "stale")
MALFORMED_PAYLOAD_MARKERS = ("malformed_payload", "malformed payload", "invalid_payload", "schema_error")
QUOTA_EXHAUSTED_MARKERS = ("quota_exhausted", "quota exhausted", "quota", "limit exhausted")
CONNECTOR_DISABLED_MARKERS = ("connector_disabled", "connector disabled", "disabled", "inactive")
TEST_DOUBLE_PROOF_MARKERS = {"mock", "mock_only", "sample", "stub", "test", "test_double"}
INTERNAL_ONLY_MODES = {"draft", "internal", "internal_only", "shadow"}


@dataclass(frozen=True)
class MarketingConnectorContractSpec:
    connector_key: str
    read_capabilities: tuple[str, ...]
    write_capabilities: tuple[str, ...] = ()
    required_read_scopes: tuple[str, ...] = ()
    required_write_scopes: tuple[str, ...] = ()
    ttl: timedelta = STALE_SYNC_AFTER


CONTRACT_SPECS: dict[str, MarketingConnectorContractSpec] = {
    "hubspot": MarketingConnectorContractSpec(
        connector_key="hubspot",
        read_capabilities=("contacts.read", "deals.read", "lists.read"),
        write_capabilities=("contacts.write", "deals.write", "workflow.enroll"),
        required_read_scopes=("crm.objects.contacts.read", "crm.objects.deals.read"),
        required_write_scopes=("automation",),
    ),
    "salesforce": MarketingConnectorContractSpec(
        connector_key="salesforce",
        read_capabilities=("leads.read", "opportunities.read", "accounts.read"),
        write_capabilities=("leads.write", "campaign_members.write"),
        required_read_scopes=("api", "refresh_token", "offline_access"),
        required_write_scopes=("api",),
    ),
    "google_ads": MarketingConnectorContractSpec(
        connector_key="google_ads",
        read_capabilities=("campaigns.read", "performance.read"),
        write_capabilities=("campaigns.mutate", "budgets.mutate"),
        required_read_scopes=("https://www.googleapis.com/auth/adwords",),
        required_write_scopes=("https://www.googleapis.com/auth/adwords",),
    ),
    "meta_ads": MarketingConnectorContractSpec(
        connector_key="meta_ads",
        read_capabilities=("campaigns.read", "insights.read"),
        write_capabilities=("campaigns.manage", "budgets.manage"),
        required_read_scopes=("ads_read", "business_management"),
        required_write_scopes=("ads_management",),
    ),
    "linkedin_ads": MarketingConnectorContractSpec(
        connector_key="linkedin_ads",
        read_capabilities=("campaigns.read", "analytics.read"),
        write_capabilities=("campaigns.write",),
        required_read_scopes=("r_ads_reporting",),
        required_write_scopes=("rw_ads",),
    ),
    "ga4": MarketingConnectorContractSpec(
        connector_key="ga4",
        read_capabilities=("analytics.read", "events.read"),
        required_read_scopes=("https://www.googleapis.com/auth/analytics.readonly",),
    ),
    "mixpanel": MarketingConnectorContractSpec(
        connector_key="mixpanel",
        read_capabilities=("events.read", "funnels.read"),
    ),
    "wordpress": MarketingConnectorContractSpec(
        connector_key="wordpress",
        read_capabilities=("posts.read", "pages.read"),
        write_capabilities=("posts.create", "posts.update", "pages.update"),
    ),
    "mailchimp": MarketingConnectorContractSpec(
        connector_key="mailchimp",
        read_capabilities=("campaigns.read", "audiences.read", "reports.read"),
        write_capabilities=("campaigns.create", "campaigns.send"),
    ),
    "sendgrid": MarketingConnectorContractSpec(
        connector_key="sendgrid",
        read_capabilities=("stats.read", "suppression.read"),
        write_capabilities=("mail.send", "templates.update"),
    ),
    "buffer": MarketingConnectorContractSpec(
        connector_key="buffer",
        read_capabilities=("profiles.read", "analytics.read"),
        write_capabilities=("updates.create",),
        required_read_scopes=("profile:read",),
        required_write_scopes=("updates:create",),
    ),
    "twitter": MarketingConnectorContractSpec(
        connector_key="twitter",
        read_capabilities=("tweets.read", "users.read"),
        write_capabilities=("tweets.write",),
        required_read_scopes=("tweet.read", "users.read", "offline.access"),
        required_write_scopes=("tweet.write",),
    ),
    "youtube": MarketingConnectorContractSpec(
        connector_key="youtube",
        read_capabilities=("channel.read", "analytics.read"),
        required_read_scopes=("https://www.googleapis.com/auth/youtube.readonly",),
    ),
    "ahrefs": MarketingConnectorContractSpec(
        connector_key="ahrefs",
        read_capabilities=("keywords.read", "backlinks.read", "site_audit.read"),
    ),
    "brandwatch": MarketingConnectorContractSpec(
        connector_key="brandwatch",
        read_capabilities=("mentions.read", "queries.read"),
        required_read_scopes=("read_mentions", "read_queries"),
    ),
    "bombora": MarketingConnectorContractSpec(
        connector_key="bombora",
        read_capabilities=("intent.read", "accounts.read"),
    ),
    "g2": MarketingConnectorContractSpec(
        connector_key="g2",
        read_capabilities=("buyer_intent.read", "reviews.read"),
    ),
    "trustradius": MarketingConnectorContractSpec(
        connector_key="trustradius",
        read_capabilities=("intent.read", "reviews.read"),
    ),
    "stripe": MarketingConnectorContractSpec(
        connector_key="stripe",
        read_capabilities=("charges.read", "customers.read"),
    ),
    "tally": MarketingConnectorContractSpec(
        connector_key="tally",
        read_capabilities=("ledger.read", "reports.read"),
    ),
}


def build_marketing_connector_contracts(
    connector_setup: Iterable[dict[str, Any]],
    connector_configs: Iterable[Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build connector contract rows for all marketing connector setup rows."""

    now = _ensure_aware(now) or datetime.now(UTC)
    configs_by_key = _configs_by_key(connector_configs)
    rows: list[dict[str, Any]] = []
    for setup_row in connector_setup:
        key = str(setup_row.get("key") or "").strip().lower()
        if not key:
            continue
        spec = CONTRACT_SPECS.get(key, _spec_from_setup(setup_row))
        rows.append(_build_contract_row(spec, setup_row, configs_by_key.get(key), now))
    return rows


def summarize_marketing_connector_contracts(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    configured_items = [
        row for row in items if row.get("configured_status") != "unconfigured"
    ]
    blocked = [row for row in configured_items if row.get("read_status") == "blocked"]
    degraded = [row for row in configured_items if row.get("read_status") == "degraded"]
    missing_write_scope = [
        row
        for row in configured_items
        if row.get("write_capabilities") and row.get("write_status") == "missing_scope"
    ]
    degraded_mode_summary = summarize_degraded_modes(configured_items)
    return {
        "total": len(items),
        "configured": len(configured_items),
        "read_ready": sum(1 for row in configured_items if row.get("read_ready")),
        "write_ready": sum(1 for row in configured_items if row.get("write_ready")),
        "write_safe": sum(1 for row in configured_items if row.get("write_safe")),
        "blocked": len(blocked),
        "degraded": len(degraded),
        "missing_write_scope": len(missing_write_scope),
        "failure_classes": sorted(
            {
                str(row.get("failure_class"))
                for row in configured_items
                if row.get("failure_class")
            }
        ),
        "degraded_mode": degraded_mode_summary,
        "write_unconfirmed": sum(
            1
            for row in configured_items
            if row.get("external_write_confirmation_status") == "write_unconfirmed"
        ),
        "write_confirmed": sum(
            1
            for row in configured_items
            if row.get("external_write_confirmation_status") == "write_confirmed"
        ),
        "mock_or_test_double": sum(1 for row in configured_items if row.get("mock_or_test_double")),
        "readiness": "blocked" if blocked else "degraded" if degraded else "ready",
    }


def evaluate_marketing_write_completion(
    connector_contracts: Iterable[dict[str, Any]],
    connector_key: str,
    action: str,
    *,
    workflow_mode: str = "active",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Decide whether a marketing external-write step can be marked complete."""

    normalized_key = _normalize_key(connector_key)
    normalized_action = _normalize_key(action)
    mode = _normalize_key(workflow_mode)
    if mode in INTERNAL_ONLY_MODES:
        return {
            "can_mark_complete": True,
            "connector_key": normalized_key,
            "action": normalized_action,
            "status": "internal_only",
            "reason": "Draft, shadow, or internal-only workflow step does not require external write confirmation.",
        }

    row = _contract_by_key(connector_contracts, normalized_key)
    if row is None:
        return {
            "can_mark_complete": False,
            "connector_key": normalized_key,
            "action": normalized_action,
            "status": "write_unconfirmed",
            "reason": "Connector contract is unavailable; external write cannot be confirmed.",
        }

    confirmation = _matching_confirmation(row, normalized_action, idempotency_key)
    if confirmation and confirmation.get("status") in {"write_confirmed", "idempotent_recovered"}:
        return {
            "can_mark_complete": True,
            "connector_key": normalized_key,
            "action": normalized_action,
            "status": confirmation.get("status"),
            "external_object_id": confirmation.get("external_object_id"),
            "source_url": confirmation.get("source_url"),
            "idempotency_key": confirmation.get("idempotency_key"),
            "request_fingerprint": confirmation.get("request_fingerprint"),
            "audit_reference": confirmation.get("audit_reference"),
            "confirmed_at": confirmation.get("confirmed_at"),
            "reason": "External write confirmation is present.",
        }

    return {
        "can_mark_complete": False,
        "connector_key": normalized_key,
        "action": normalized_action,
        "status": "write_unconfirmed",
        "idempotency_key": idempotency_key,
        "reason": "External write confirmation is missing; do not mark this step complete.",
    }


def plan_marketing_write_retry(
    connector_contracts: Iterable[dict[str, Any]],
    connector_key: str,
    action: str,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Project safe retry metadata for a marketing external write."""

    normalized_key = _normalize_key(connector_key)
    normalized_action = _normalize_key(action)
    row = _contract_by_key(connector_contracts, normalized_key)
    if row is None:
        return {
            "safe_to_retry": False,
            "connector_key": normalized_key,
            "action": normalized_action,
            "reason": "Connector contract is unavailable.",
        }

    retry_budget = row.get("retry_budget") if isinstance(row.get("retry_budget"), dict) else {}
    retry_policy = row.get("retry_policy") if isinstance(row.get("retry_policy"), dict) else {}
    stored_key = _string_or_none(idempotency_key) or _string_or_none(retry_budget.get("idempotency_key"))
    confirmation = _matching_confirmation(row, normalized_action, stored_key)

    if confirmation and confirmation.get("status") in {"write_confirmed", "idempotent_recovered"}:
        return {
            "safe_to_retry": False,
            "connector_key": normalized_key,
            "action": normalized_action,
            "idempotency_key": confirmation.get("idempotency_key"),
            "reason": "External write is already confirmed; duplicate retry should not run.",
            "duplicate_policy": "already_confirmed",
        }

    if row.get("failure_class") and not retry_policy.get("retryable"):
        return {
            "safe_to_retry": False,
            "connector_key": normalized_key,
            "action": normalized_action,
            "idempotency_key": stored_key,
            "reason": (
                f"Connector failure class '{row.get('failure_class')}' is not retryable; "
                "an operator action is required before retry."
            ),
            "duplicate_policy": "not_retryable",
            "failure_class": row.get("failure_class"),
            "audit_event_code": retry_policy.get("audit_event_code"),
        }

    requires_idempotency = bool(retry_policy.get("safe_retry_requires_idempotency")) or not row.get("failure_class")
    if requires_idempotency and not row.get("idempotency_key_supported"):
        return {
            "safe_to_retry": False,
            "connector_key": normalized_key,
            "action": normalized_action,
            "reason": "Connector contract does not prove idempotency-key support.",
            "duplicate_policy": "blocked_without_idempotency",
        }

    if requires_idempotency and not stored_key:
        return {
            "safe_to_retry": False,
            "connector_key": normalized_key,
            "action": normalized_action,
            "reason": "Safe retry requires a stable idempotency key.",
            "duplicate_policy": "blocked_without_idempotency",
            "failure_class": row.get("failure_class"),
            "audit_event_code": retry_policy.get("audit_event_code"),
        }

    remaining = _int_or_zero(retry_budget.get("remaining_attempts"))
    if remaining <= 0:
        return {
            "safe_to_retry": False,
            "connector_key": normalized_key,
            "action": normalized_action,
            "idempotency_key": stored_key,
            "reason": "Retry budget is exhausted.",
            "duplicate_policy": "retry_budget_exhausted",
        }

    return {
        "safe_to_retry": True,
        "connector_key": normalized_key,
        "action": normalized_action,
        "idempotency_key": stored_key,
        "remaining_attempts": remaining,
        "next_retry_at": retry_budget.get("next_retry_at"),
        "backoff_strategy": retry_budget.get("backoff_strategy"),
        "failure_class": row.get("failure_class"),
        "audit_event_code": retry_budget.get("audit_event_code"),
        "reason": "Retry can reuse the same idempotency key within the recorded retry budget.",
        "duplicate_policy": "reuse_idempotency_key",
    }


def _build_contract_row(
    spec: MarketingConnectorContractSpec,
    setup_row: dict[str, Any],
    config: Any | None,
    now: datetime,
) -> dict[str, Any]:
    config_dict = _config_dict(config)
    contract = _contract_payload(config_dict)
    configured = str(setup_row.get("configured_status") or "") != "unconfigured"
    granted_scopes = _scopes_from_contract_or_config(contract, config_dict)
    read_capabilities = _tuple_from_payload(contract, "read_capabilities", spec.read_capabilities)
    write_capabilities = _tuple_from_payload(contract, "write_capabilities", spec.write_capabilities)
    required_read_scopes = _tuple_from_payload(contract, "required_read_scopes", spec.required_read_scopes)
    required_write_scopes = _tuple_from_payload(contract, "required_write_scopes", spec.required_write_scopes)
    missing_read_scopes = _missing_scopes(required_read_scopes, granted_scopes)
    missing_write_scopes = _missing_scopes(required_write_scopes, granted_scopes)
    last_sync_at = _last_sync_at(config, contract)
    ttl_seconds = _ttl_seconds(contract, spec.ttl)
    freshness_status = _freshness_status(last_sync_at, ttl_seconds, now)
    confirmations = _write_confirmations(contract)
    confirmation_status = _external_write_confirmation_status(confirmations)
    mock_or_test_double = _mock_or_test_double(contract, config_dict)
    auth_status = _auth_status(contract, setup_row, config_dict)
    contract_state = _contract_state(
        contract,
        setup_row,
        auth_status,
        freshness_status,
        missing_read_scopes,
        missing_write_scopes,
        mock_or_test_double,
    )
    failure_class = _failure_class(
        contract,
        contract_state,
        configured,
        missing_read_scopes,
        missing_write_scopes,
        mock_or_test_double,
    )
    retry_policy = policy_projection(failure_class)
    retry_budget = _retry_budget(contract, retry_policy)
    production_ready = configured and not mock_or_test_double
    read_status = _read_status(
        configured,
        production_ready,
        contract_state,
        missing_read_scopes,
        missing_write_scopes,
        read_capabilities,
    )
    write_status = _write_status(
        read_status,
        contract_state,
        write_capabilities,
        missing_write_scopes,
        bool(retry_budget.get("idempotency_supported")),
    )
    degraded_reason = _degraded_reason(
        contract,
        contract_state,
        mock_or_test_double,
        missing_read_scopes,
        missing_write_scopes,
        retry_budget,
    )
    next_action_cta = _next_action_cta(contract_state, read_status, write_status, retry_policy)
    row_blocks_kpi_confidence = bool(retry_policy.get("blocks_production_kpi_confidence")) and read_status != "ready"
    blocks_external_writes = bool(write_capabilities) and (
        bool(retry_policy.get("blocks_external_writes")) or write_status != "ready"
    )
    write_safe = write_status == "ready" and not blocks_external_writes
    degraded_mode = build_degraded_mode_projection(
        connector_key=spec.connector_key,
        connector_name=_string_or_none(setup_row.get("name")),
        category=_string_or_none(setup_row.get("category")),
        failure_class=failure_class,
        reason=degraded_reason,
        policy={
            **retry_policy,
            "blocks_production_kpi_confidence": row_blocks_kpi_confidence,
            "blocks_external_writes": blocks_external_writes,
        },
        read_status=read_status,
        write_status=write_status,
        next_action_cta=next_action_cta,
    )

    return {
        "connector_key": spec.connector_key,
        "name": setup_row.get("name"),
        "category": setup_row.get("category"),
        "configured_status": setup_row.get("configured_status"),
        "vendor_account_id": _account_id_from_config(config_dict),
        "workspace_id": _workspace_id_from_config(config_dict),
        "read_capabilities": list(read_capabilities),
        "write_capabilities": list(write_capabilities),
        "required_read_scopes": list(required_read_scopes),
        "required_write_scopes": list(required_write_scopes),
        "required_scopes": sorted(set(required_read_scopes).union(required_write_scopes)),
        "granted_scopes": granted_scopes,
        "missing_read_scopes": missing_read_scopes,
        "missing_write_scopes": missing_write_scopes,
        "missing_scopes": sorted(set(missing_read_scopes).union(missing_write_scopes)),
        "auth_status": auth_status,
        "health_status": setup_row.get("health_status"),
        "contract_state": contract_state,
        "failure_class": failure_class,
        "read_status": read_status,
        "write_status": write_status,
        "read_ready": read_status == "ready",
        "write_ready": write_status == "ready",
        "write_safe": write_safe,
        "production_ready": production_ready,
        "mock_or_test_double": mock_or_test_double,
        "last_sync_at": _isoformat(last_sync_at),
        "source_objects": _source_objects(contract, config_dict),
        "data_freshness": {
            "status": freshness_status,
            "ttl_seconds": ttl_seconds,
            "last_sync_at": _isoformat(last_sync_at),
        },
        "retry_budget": retry_budget,
        "retry_policy": retry_policy,
        "degraded_mode_reason": degraded_reason,
        "degraded_mode": degraded_mode,
        "confidence_impact": degraded_mode["confidence_impact"],
        "blocks_external_writes": blocks_external_writes,
        "blocks_production_kpi_confidence": row_blocks_kpi_confidence,
        "idempotency_key_supported": bool(retry_budget.get("idempotency_supported")),
        "external_write_confirmation_status": confirmation_status,
        "external_write_confirmations": confirmations,
        "next_action_cta": next_action_cta,
    }


def _spec_from_setup(setup_row: dict[str, Any]) -> MarketingConnectorContractSpec:
    key = str(setup_row.get("key") or "").strip().lower()
    requirement = next((item for item in MARKETING_CONNECTOR_REQUIREMENTS if item.key == key), None)
    return MarketingConnectorContractSpec(
        connector_key=key,
        read_capabilities=(f"{key}.read",),
        required_read_scopes=tuple(requirement.required_scopes if requirement else ()),
    )


def _contract_payload(config: dict[str, Any]) -> dict[str, Any]:
    value = (
        config.get("marketing_connector_contract")
        or config.get("marketing_contract")
        or config.get("connector_contract")
        or {}
    )
    return value if isinstance(value, dict) else {}


def _contract_state(
    contract: dict[str, Any],
    setup_row: dict[str, Any],
    auth_status: str,
    freshness_status: str,
    missing_read_scopes: list[str],
    missing_write_scopes: list[str],
    mock_or_test_double: bool,
) -> str:
    raw = _normalize_key(
        contract.get("state")
        or contract.get("contract_state")
        or contract.get("status")
        or contract.get("health_status")
        or setup_row.get("health_status")
    )
    status_text = " ".join(
        str(part or "")
        for part in (
            raw,
            contract.get("degraded_mode_reason"),
            contract.get("last_error"),
            setup_row.get("detail"),
        )
    ).lower()

    if mock_or_test_double:
        return "degraded"
    if _contains_any(status_text, CONNECTOR_DISABLED_MARKERS):
        return "connector_disabled"
    if auth_status == "expired" or _contains_any(status_text, AUTH_EXPIRED_MARKERS):
        return "auth_expired"
    if missing_read_scopes or _contains_any(status_text, MISSING_SCOPE_MARKERS):
        return "missing_scope"
    if _contains_any(status_text, RATE_LIMIT_MARKERS):
        return "rate_limited"
    if _contains_any(status_text, TIMEOUT_MARKERS):
        return "timeout"
    if _contains_any(status_text, VENDOR_5XX_MARKERS):
        return "vendor_5xx"
    if _contains_any(status_text, MALFORMED_PAYLOAD_MARKERS):
        return "malformed_payload"
    if _contains_any(status_text, QUOTA_EXHAUSTED_MARKERS):
        return "quota_exhausted"
    if _contains_any(status_text, PARTIAL_DATA_MARKERS):
        return "partial_data"
    if freshness_status == "stale" or _contains_any(status_text, STALE_DATA_MARKERS):
        return "stale_data"
    if raw in CONTRACT_STATES:
        return raw
    if missing_write_scopes:
        return "missing_scope"
    if raw in {"healthy", "ok", "ready"}:
        return "healthy"
    return "degraded"


def _failure_class(
    contract: dict[str, Any],
    contract_state: str,
    configured: bool,
    missing_read_scopes: list[str],
    missing_write_scopes: list[str],
    mock_or_test_double: bool,
) -> str | None:
    if not configured:
        return None
    explicit = normalize_failure_class(
        contract.get("failure_class")
        or contract.get("failure")
        or contract.get("error_class")
        or contract_state
    )
    if explicit:
        return explicit
    if missing_read_scopes or missing_write_scopes or contract_state == "missing_scope":
        return "insufficient_scope"
    if mock_or_test_double:
        return None
    return None


def _read_status(
    configured: bool,
    production_ready: bool,
    contract_state: str,
    missing_read_scopes: list[str],
    missing_write_scopes: list[str],
    read_capabilities: tuple[str, ...],
) -> str:
    if not configured or not production_ready or not read_capabilities:
        return "blocked"
    unknown_scope_blocker = contract_state == "missing_scope" and not missing_write_scopes
    hard_blocked_state = contract_state in {
        "auth_expired",
        "connector_disabled",
        "malformed_payload",
    }
    if hard_blocked_state or missing_read_scopes or unknown_scope_blocker:
        return "blocked"
    if contract_state in {
        "rate_limited",
        "timeout",
        "vendor_5xx",
        "partial_data",
        "stale_data",
        "quota_exhausted",
        "degraded",
    }:
        return "degraded"
    return "ready"


def _write_status(
    read_status: str,
    contract_state: str,
    write_capabilities: tuple[str, ...],
    missing_write_scopes: list[str],
    idempotency_supported: bool,
) -> str:
    if not write_capabilities:
        return "read_only"
    if read_status == "blocked":
        return "blocked"
    if missing_write_scopes or contract_state == "missing_scope":
        return "missing_scope"
    if read_status == "degraded":
        return "degraded"
    if not idempotency_supported:
        return "idempotency_missing"
    return "ready"


def _degraded_reason(
    contract: dict[str, Any],
    contract_state: str,
    mock_or_test_double: bool,
    missing_read_scopes: list[str],
    missing_write_scopes: list[str],
    retry_budget: dict[str, Any],
) -> str | None:
    explicit = _string_or_none(
        contract.get("degraded_mode_reason")
        or contract.get("blocking_reason")
        or contract.get("last_error")
    )
    if explicit:
        return explicit
    if mock_or_test_double:
        return "Connector contract is marked as test-double/mock-only and cannot prove production readiness."
    if missing_read_scopes:
        return f"Missing read scopes: {', '.join(missing_read_scopes)}."
    if missing_write_scopes:
        return f"Missing write scopes: {', '.join(missing_write_scopes)}."
    if contract_state == "rate_limited":
        return f"Connector is rate limited; next retry at {retry_budget.get('next_retry_at') or 'unknown'}."
    if contract_state == "timeout":
        return "Connector timed out; retry budget metadata is required before retrying."
    if contract_state == "vendor_5xx":
        return "Vendor returned a 5xx response; workflow should use degraded mode."
    if contract_state == "malformed_payload":
        return "Connector returned a malformed payload; fix the contract/parser before using production data."
    if contract_state == "quota_exhausted":
        return "Connector quota is exhausted; wait for the reset window before retrying."
    if contract_state == "connector_disabled":
        return "Connector is disabled and must be enabled before CMO workflows can use it."
    if contract_state == "partial_data":
        return "Connector returned partial data."
    if contract_state == "stale_data":
        return "Connector data is stale beyond its TTL."
    if contract_state == "auth_expired":
        return "Connector auth is expired and must be reauthorized."
    if contract_state == "degraded":
        return "Connector contract is degraded."
    return None


def _next_action_cta(
    contract_state: str,
    read_status: str,
    write_status: str,
    retry_policy: dict[str, Any] | None = None,
) -> str:
    if retry_policy and retry_policy.get("required_cta") not in {None, "", "none"}:
        return str(retry_policy["required_cta"])
    if contract_state == "auth_expired":
        return "reconnect"
    if contract_state == "missing_scope" or write_status == "missing_scope":
        return "add_scope"
    if contract_state in {"rate_limited", "timeout", "vendor_5xx"}:
        return "review_retry_budget"
    if contract_state == "malformed_payload":
        return "fix_connector_payload"
    if contract_state == "quota_exhausted":
        return "wait_for_quota_reset"
    if contract_state == "connector_disabled":
        return "enable_connector"
    if contract_state in {"partial_data", "stale_data", "degraded"}:
        return "review_degraded"
    if write_status == "idempotency_missing":
        return "configure_idempotency"
    if read_status == "blocked":
        return "setup"
    return "none"


def _auth_status(
    contract: dict[str, Any],
    setup_row: dict[str, Any],
    config: dict[str, Any],
) -> str:
    raw = str(
        contract.get("auth_status")
        or config.get("auth_state")
        or setup_row.get("auth_status")
        or ""
    ).strip().lower()
    health = str(setup_row.get("health_status") or "").strip().lower()
    if raw in {"expired", "auth_expired", "token_expired"} or health == "expired_auth":
        return "expired"
    if raw in {"missing", "unconfigured"} or str(setup_row.get("configured_status") or "") == "unconfigured":
        return "missing"
    if raw in {"valid", "healthy", "ok", "ready"} or health in {"healthy", "stale", "degraded"}:
        return "valid"
    return raw or "unknown"


def _retry_budget(contract: dict[str, Any], retry_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    value = contract.get("retry_budget") or contract.get("retry") or {}
    raw = value if isinstance(value, dict) else {}
    policy = retry_policy or {}
    max_attempts = _int_or_zero(
        raw.get("max_attempts")
        or raw.get("budget")
        or policy.get("max_attempts")
        or 0
    )
    attempts_used = _int_or_zero(raw.get("attempts_used") or raw.get("used") or 0)
    remaining = raw.get("remaining_attempts")
    remaining_attempts = _int_or_zero(remaining) if remaining is not None else max(max_attempts - attempts_used, 0)
    return {
        "max_attempts": max_attempts,
        "attempts_used": attempts_used,
        "remaining_attempts": remaining_attempts,
        "reset_at": _string_or_none(raw.get("reset_at")),
        "next_retry_at": _string_or_none(raw.get("next_retry_at")),
        "last_error": _string_or_none(raw.get("last_error")),
        "idempotency_key": _string_or_none(
            raw.get("idempotency_key")
            or contract.get("idempotency_key")
        ),
        "idempotency_supported": bool(
            raw.get("idempotency_supported")
            or raw.get("supports_idempotency")
            or contract.get("idempotency_supported")
            or contract.get("supports_idempotency")
        ),
        "retryable": bool(policy.get("retryable")),
        "backoff_strategy": policy.get("backoff_strategy") or {"type": "none"},
        "safe_retry_requires_idempotency": bool(policy.get("safe_retry_requires_idempotency")),
        "audit_event_code": _string_or_none(policy.get("audit_event_code")),
    }


def _write_confirmations(contract: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (
        contract.get("external_write_confirmations")
        or contract.get("write_confirmations")
        or contract.get("write_confirmation")
        or {}
    )
    confirmations: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        iterable = raw.items()
    elif isinstance(raw, list):
        iterable = [(item.get("action") if isinstance(item, dict) else None, item) for item in raw]
    else:
        iterable = []
    for action, payload in iterable:
        if not isinstance(payload, dict):
            continue
        status = _normalize_key(payload.get("status") or payload.get("confirmation_status") or "")
        if status not in WRITE_CONFIRMATION_STATES:
            status = "write_confirmed" if payload.get("confirmed") is True else "write_unconfirmed"
        confirmations.append(
            {
                "action": _normalize_key(payload.get("action") or action),
                "status": status,
                "idempotency_key": _string_or_none(payload.get("idempotency_key")),
                "external_object_id": _string_or_none(
                    payload.get("external_object_id")
                    or payload.get("source_object_id")
                    or payload.get("id")
                ),
                "source_url": _string_or_none(payload.get("source_url") or payload.get("url")),
                "confirmed_at": _string_or_none(payload.get("confirmed_at")),
                "request_fingerprint": _string_or_none(
                    payload.get("request_fingerprint")
                    or payload.get("request_hash")
                ),
                "audit_reference": _string_or_none(
                    payload.get("audit_reference")
                    or payload.get("audit_ref")
                ),
                "actor_id": _string_or_none(payload.get("actor_id")),
                "agent_id": _string_or_none(payload.get("agent_id")),
                "workflow_id": _string_or_none(payload.get("workflow_id")),
                "workflow_run_id": _string_or_none(payload.get("workflow_run_id")),
                "run_id": _string_or_none(payload.get("run_id")),
            }
        )
    return confirmations


def _external_write_confirmation_status(confirmations: list[dict[str, Any]]) -> str:
    if any(
        row.get("status")
        in {"accepted", "rejected", "timeout_unknown", "retry_scheduled", "write_unconfirmed"}
        for row in confirmations
    ):
        return "write_unconfirmed"
    if any(row.get("status") in {"write_confirmed", "idempotent_recovered"} for row in confirmations):
        return "write_confirmed"
    return "none"


def _matching_confirmation(
    contract_row: dict[str, Any],
    action: str,
    idempotency_key: str | None,
) -> dict[str, Any] | None:
    confirmations = contract_row.get("external_write_confirmations") or []
    if not isinstance(confirmations, list):
        return None
    normalized_action = _normalize_key(action)
    normalized_idempotency = _string_or_none(idempotency_key)
    for confirmation in confirmations:
        if not isinstance(confirmation, dict):
            continue
        if _normalize_key(confirmation.get("action")) != normalized_action:
            continue
        if normalized_idempotency and confirmation.get("idempotency_key") != normalized_idempotency:
            continue
        return confirmation
    return None


def _contract_by_key(
    connector_contracts: Iterable[dict[str, Any]],
    connector_key: str,
) -> dict[str, Any] | None:
    normalized_key = _normalize_key(connector_key)
    for row in connector_contracts:
        if _normalize_key(row.get("connector_key")) == normalized_key:
            return row
    return None


def _source_objects(contract: dict[str, Any], config: dict[str, Any]) -> list[dict[str, str | None]]:
    raw = contract.get("source_objects") or config.get("source_objects") or []
    items: list[dict[str, str | None]] = []
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "source_object_id": _string_or_none(
                        item.get("source_object_id") or item.get("id")
                    ),
                    "source_url": _string_or_none(item.get("source_url") or item.get("url")),
                    "object_type": _string_or_none(item.get("object_type") or item.get("type")),
                }
            )
    object_id = _string_or_none(config.get("source_object_id") or contract.get("source_object_id"))
    object_url = _string_or_none(config.get("source_url") or contract.get("source_url"))
    if object_id or object_url:
        items.append(
            {
                "source_object_id": object_id,
                "source_url": object_url,
                "object_type": _string_or_none(config.get("source_object_type") or contract.get("source_object_type")),
            }
        )
    return items


def _freshness_status(last_sync_at: datetime | None, ttl_seconds: int, now: datetime) -> str:
    if last_sync_at is None:
        return "missing"
    if ttl_seconds <= 0:
        return "fresh"
    return "stale" if now - last_sync_at > timedelta(seconds=ttl_seconds) else "fresh"


def _ttl_seconds(contract: dict[str, Any], fallback: timedelta) -> int:
    raw = contract.get("ttl_seconds") or contract.get("data_ttl_seconds")
    if raw is None:
        return int(fallback.total_seconds())
    return _int_or_zero(raw)


def _last_sync_at(config: Any | None, contract: dict[str, Any]) -> datetime | None:
    return _parse_datetime(contract.get("last_sync_at") or contract.get("last_successful_sync_at")) or _ensure_aware(
        getattr(config, "last_sync_at", None)
    )


def _mock_or_test_double(contract: dict[str, Any], config: dict[str, Any]) -> bool:
    if any(bool(contract.get(key) or config.get(key)) for key in ("is_test_double", "mock_only", "stub_only")):
        return True
    proof = _normalize_key(contract.get("production_proof") or config.get("production_proof"))
    environment = _normalize_key(contract.get("environment") or config.get("environment"))
    return proof in TEST_DOUBLE_PROOF_MARKERS or environment in TEST_DOUBLE_PROOF_MARKERS


def _tuple_from_payload(
    contract: dict[str, Any],
    key: str,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    value = contract.get(key)
    if value is None:
        return fallback
    return tuple(str(item) for item in _list_from_value(value) if str(item).strip())


def _scopes_from_contract_or_config(contract: dict[str, Any], config: dict[str, Any]) -> list[str]:
    value = (
        contract.get("granted_scopes")
        or contract.get("validated_scopes")
        or config.get("granted_scopes")
        or config.get("validated_scopes")
        or config.get("scopes")
        or config.get("scope")
    )
    return [str(item) for item in _list_from_value(value) if str(item).strip()]


def _missing_scopes(required: tuple[str, ...], granted: list[str]) -> list[str]:
    granted_set = set(granted)
    return [scope for scope in required if scope not in granted_set]


def _configs_by_key(connector_configs: Iterable[Any]) -> dict[str, Any]:
    return {
        str(getattr(config, "connector_name", "") or "").strip().lower(): config
        for config in connector_configs
    }


def _config_dict(config: Any | None) -> dict[str, Any]:
    value = getattr(config, "config", None)
    return value if isinstance(value, dict) else {}


def _account_id_from_config(config: dict[str, Any]) -> str | None:
    for key in (
        "account_id",
        "customer_id",
        "ad_account_id",
        "property_id",
        "site_url",
        "project_id",
        "vendor_id",
        "channel_id",
        "company_name",
    ):
        value = _string_or_none(config.get(key))
        if value:
            return value
    return None


def _workspace_id_from_config(config: dict[str, Any]) -> str | None:
    for key in ("workspace_id", "organization_id", "business_id", "account_id"):
        value = _string_or_none(config.get(key))
        if value:
            return value
    return None


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def _list_from_value(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [part.strip() for part in value.replace(",", " ").split() if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


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


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
