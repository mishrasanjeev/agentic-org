"""CMO marketing connector retry and degraded-mode policy.

This module is intentionally vendor-neutral. It gives connector setup,
contracts, data readiness, workflow activation, write completion, and workflow
linting the same failure-class vocabulary so production CMO paths do not treat
partial, stale, or failed connector data as healthy.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from core.marketing.decision_audit import build_connector_degraded_decision_audit
from core.marketing.escalation_matrix import evaluate_marketing_escalation

CONNECTOR_FAILURE_CLASSES = (
    "timeout",
    "rate_limited",
    "vendor_5xx",
    "auth_expired",
    "insufficient_scope",
    "partial_data",
    "stale_data",
    "malformed_payload",
    "quota_exhausted",
    "connector_disabled",
)

FAILURE_CLASS_ALIASES = {
    "auth_expired": "auth_expired",
    "expired_auth": "auth_expired",
    "token_expired": "auth_expired",
    "missing_scope": "insufficient_scope",
    "insufficient_scope": "insufficient_scope",
    "forbidden": "insufficient_scope",
    "rate_limit": "rate_limited",
    "rate_limited": "rate_limited",
    "rate_limited_429": "rate_limited",
    "timeout": "timeout",
    "timed_out": "timeout",
    "deadline_exceeded": "timeout",
    "vendor_5xx": "vendor_5xx",
    "server_error": "vendor_5xx",
    "vendor_server_error": "vendor_5xx",
    "partial": "partial_data",
    "partial_data": "partial_data",
    "stale": "stale_data",
    "stale_data": "stale_data",
    "malformed": "malformed_payload",
    "malformed_payload": "malformed_payload",
    "invalid_payload": "malformed_payload",
    "schema_error": "malformed_payload",
    "quota": "quota_exhausted",
    "quota_exhausted": "quota_exhausted",
    "disabled": "connector_disabled",
    "connector_disabled": "connector_disabled",
}


@dataclass(frozen=True)
class ConnectorFailurePolicy:
    failure_class: str | None
    retryable: bool
    max_attempts: int
    backoff_strategy: dict[str, Any]
    safe_retry_requires_idempotency: bool
    degraded_mode_allowed: bool
    confidence_impact: float
    blocks_external_writes: bool
    blocks_production_kpi_confidence: bool
    required_cta: str
    audit_event_code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "retryable": self.retryable,
            "max_attempts": self.max_attempts,
            "backoff_strategy": dict(self.backoff_strategy),
            "safe_retry_requires_idempotency": self.safe_retry_requires_idempotency,
            "degraded_mode_allowed": self.degraded_mode_allowed,
            "confidence_impact": self.confidence_impact,
            "blocks_external_writes": self.blocks_external_writes,
            "blocks_production_kpi_confidence": self.blocks_production_kpi_confidence,
            "required_cta": self.required_cta,
            "audit_event_code": self.audit_event_code,
        }


NO_FAILURE_POLICY = ConnectorFailurePolicy(
    failure_class=None,
    retryable=False,
    max_attempts=0,
    backoff_strategy={"type": "none"},
    safe_retry_requires_idempotency=False,
    degraded_mode_allowed=False,
    confidence_impact=0.0,
    blocks_external_writes=False,
    blocks_production_kpi_confidence=False,
    required_cta="none",
    audit_event_code="cmo_connector_healthy",
)

CONNECTOR_FAILURE_POLICIES: dict[str, ConnectorFailurePolicy] = {
    "timeout": ConnectorFailurePolicy(
        failure_class="timeout",
        retryable=True,
        max_attempts=3,
        backoff_strategy={"type": "exponential_jitter", "initial_seconds": 30, "max_seconds": 900},
        safe_retry_requires_idempotency=True,
        degraded_mode_allowed=True,
        confidence_impact=0.30,
        blocks_external_writes=True,
        blocks_production_kpi_confidence=False,
        required_cta="review_retry_budget",
        audit_event_code="cmo_connector_timeout",
    ),
    "rate_limited": ConnectorFailurePolicy(
        failure_class="rate_limited",
        retryable=True,
        max_attempts=5,
        backoff_strategy={"type": "retry_after_or_exponential", "initial_seconds": 60, "max_seconds": 3600},
        safe_retry_requires_idempotency=True,
        degraded_mode_allowed=True,
        confidence_impact=0.25,
        blocks_external_writes=True,
        blocks_production_kpi_confidence=False,
        required_cta="review_retry_budget",
        audit_event_code="cmo_connector_rate_limited",
    ),
    "vendor_5xx": ConnectorFailurePolicy(
        failure_class="vendor_5xx",
        retryable=True,
        max_attempts=3,
        backoff_strategy={"type": "exponential_jitter", "initial_seconds": 120, "max_seconds": 1800},
        safe_retry_requires_idempotency=True,
        degraded_mode_allowed=True,
        confidence_impact=0.35,
        blocks_external_writes=True,
        blocks_production_kpi_confidence=False,
        required_cta="monitor_vendor_recovery",
        audit_event_code="cmo_connector_vendor_5xx",
    ),
    "auth_expired": ConnectorFailurePolicy(
        failure_class="auth_expired",
        retryable=False,
        max_attempts=0,
        backoff_strategy={"type": "none"},
        safe_retry_requires_idempotency=False,
        degraded_mode_allowed=False,
        confidence_impact=1.0,
        blocks_external_writes=True,
        blocks_production_kpi_confidence=True,
        required_cta="reconnect",
        audit_event_code="cmo_connector_auth_expired",
    ),
    "insufficient_scope": ConnectorFailurePolicy(
        failure_class="insufficient_scope",
        retryable=False,
        max_attempts=0,
        backoff_strategy={"type": "none"},
        safe_retry_requires_idempotency=False,
        degraded_mode_allowed=False,
        confidence_impact=1.0,
        blocks_external_writes=True,
        blocks_production_kpi_confidence=True,
        required_cta="add_scope",
        audit_event_code="cmo_connector_insufficient_scope",
    ),
    "partial_data": ConnectorFailurePolicy(
        failure_class="partial_data",
        retryable=False,
        max_attempts=0,
        backoff_strategy={"type": "none"},
        safe_retry_requires_idempotency=False,
        degraded_mode_allowed=True,
        confidence_impact=0.45,
        blocks_external_writes=True,
        blocks_production_kpi_confidence=False,
        required_cta="review_degraded",
        audit_event_code="cmo_connector_partial_data",
    ),
    "stale_data": ConnectorFailurePolicy(
        failure_class="stale_data",
        retryable=True,
        max_attempts=2,
        backoff_strategy={"type": "refresh_then_fixed", "initial_seconds": 300, "max_seconds": 1800},
        safe_retry_requires_idempotency=False,
        degraded_mode_allowed=True,
        confidence_impact=0.40,
        blocks_external_writes=True,
        blocks_production_kpi_confidence=False,
        required_cta="refresh_connector",
        audit_event_code="cmo_connector_stale_data",
    ),
    "malformed_payload": ConnectorFailurePolicy(
        failure_class="malformed_payload",
        retryable=False,
        max_attempts=0,
        backoff_strategy={"type": "none"},
        safe_retry_requires_idempotency=False,
        degraded_mode_allowed=False,
        confidence_impact=1.0,
        blocks_external_writes=True,
        blocks_production_kpi_confidence=True,
        required_cta="fix_connector_payload",
        audit_event_code="cmo_connector_malformed_payload",
    ),
    "quota_exhausted": ConnectorFailurePolicy(
        failure_class="quota_exhausted",
        retryable=True,
        max_attempts=1,
        backoff_strategy={"type": "quota_reset_window", "initial_seconds": 3600, "max_seconds": 86400},
        safe_retry_requires_idempotency=True,
        degraded_mode_allowed=True,
        confidence_impact=0.35,
        blocks_external_writes=True,
        blocks_production_kpi_confidence=False,
        required_cta="wait_for_quota_reset",
        audit_event_code="cmo_connector_quota_exhausted",
    ),
    "connector_disabled": ConnectorFailurePolicy(
        failure_class="connector_disabled",
        retryable=False,
        max_attempts=0,
        backoff_strategy={"type": "none"},
        safe_retry_requires_idempotency=False,
        degraded_mode_allowed=False,
        confidence_impact=1.0,
        blocks_external_writes=True,
        blocks_production_kpi_confidence=True,
        required_cta="enable_connector",
        audit_event_code="cmo_connector_disabled",
    ),
}

KPI_BY_CONNECTOR_CATEGORY: dict[str, tuple[str, ...]] = {
    "CRM": ("MQL", "SQL", "Pipeline contribution", "Lead nurture readiness"),
    "Ads": ("ROAS", "CAC", "Attribution", "Spend optimization"),
    "Analytics": ("Attribution", "Campaign performance", "Reporting freshness"),
    "CMS": ("Content pipeline", "Attribution"),
    "Email": ("Email performance", "Lead nurture readiness"),
    "Social": ("Social engagement", "Campaign performance"),
    "SEO": ("Organic pipeline", "Search visibility"),
    "Brand": ("Brand sentiment", "Brand risk"),
    "ABM": ("ABM readiness", "Pipeline contribution"),
    "Finance": ("CAC", "ROAS", "Pipeline contribution"),
}


def normalize_failure_class(value: Any) -> str | None:
    normalized = _normalize_key(value)
    if not normalized or normalized in {"healthy", "ok", "ready", "write_confirmed"}:
        return None
    if normalized in CONNECTOR_FAILURE_CLASSES:
        return normalized
    return FAILURE_CLASS_ALIASES.get(normalized)


def policy_for_failure(failure_class: Any) -> ConnectorFailurePolicy:
    normalized = normalize_failure_class(failure_class)
    if normalized is None:
        return NO_FAILURE_POLICY
    return CONNECTOR_FAILURE_POLICIES[normalized]


def policy_projection(failure_class: Any) -> dict[str, Any]:
    return policy_for_failure(failure_class).to_dict()


def affected_kpis_for_category(category: Any) -> list[str]:
    return list(KPI_BY_CONNECTOR_CATEGORY.get(str(category or "").strip(), ()))


def build_degraded_mode_projection(
    *,
    connector_key: str,
    connector_name: str | None,
    category: str | None,
    failure_class: str | None,
    reason: str | None,
    policy: dict[str, Any],
    read_status: str,
    write_status: str,
    next_action_cta: str,
    affected_workflows: Iterable[str] = (),
) -> dict[str, Any]:
    blocked = (
        bool(failure_class)
        and not bool(policy.get("degraded_mode_allowed"))
        and (read_status == "blocked" or write_status in {"blocked", "missing_scope"})
    )
    active = bool(failure_class) and bool(policy.get("degraded_mode_allowed"))
    active = active or read_status == "degraded" or write_status == "degraded"
    status = "blocked" if blocked else "degraded" if active else "none"
    escalation_decision = (
        evaluate_marketing_escalation(
            {
                "trigger_type": (
                    "connector_auth_expired"
                    if failure_class == "auth_expired"
                    else "connector_degraded"
                ),
                "connector_key": connector_key,
                "connector_name": connector_name,
                "failure_class": failure_class,
                "severity": "high" if blocked else "medium",
                "reason": reason,
            }
        )
        if failure_class
        else None
    )
    projection = {
        "status": status,
        "active": active,
        "allowed": bool(policy.get("degraded_mode_allowed")),
        "reason": reason,
        "failure_class": failure_class,
        "affected_connectors": [connector_key] if connector_key else [],
        "affected_connector_names": [connector_name] if connector_name else [],
        "affected_workflows": sorted({str(item) for item in affected_workflows if str(item).strip()}),
        "affected_kpis": affected_kpis_for_category(category),
        "affected_capability": category,
        "confidence_impact": float(policy.get("confidence_impact") or 0.0),
        "blocks_external_writes": bool(policy.get("blocks_external_writes")),
        "blocks_production_kpi_confidence": bool(policy.get("blocks_production_kpi_confidence")),
        "next_action_cta": next_action_cta,
        "audit_event_code": str(policy.get("audit_event_code") or "cmo_connector_healthy"),
        "escalation_decision": escalation_decision,
        "escalation_evidence": escalation_decision.get("evidence") if escalation_decision else None,
    }
    if failure_class:
        audit_package = build_connector_degraded_decision_audit(projection)
        projection["decision_audit"] = audit_package
        projection["decision_audit_ref"] = audit_package["audit_reference"]
    return projection


def summarize_degraded_modes(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    degraded_modes = [
        row.get("degraded_mode")
        for row in rows
        if isinstance(row.get("degraded_mode"), dict)
        and row["degraded_mode"].get("status") in {"degraded", "blocked"}
    ]
    affected_connectors = {
        connector
        for mode in degraded_modes
        for connector in mode.get("affected_connectors") or []
        if connector
    }
    affected_workflows = {
        workflow
        for mode in degraded_modes
        for workflow in mode.get("affected_workflows") or []
        if workflow
    }
    affected_kpis = {
        kpi
        for mode in degraded_modes
        for kpi in mode.get("affected_kpis") or []
        if kpi
    }
    return {
        "active": bool(degraded_modes),
        "blocked": sum(1 for mode in degraded_modes if mode.get("status") == "blocked"),
        "degraded": sum(1 for mode in degraded_modes if mode.get("status") == "degraded"),
        "affected_connectors": sorted(affected_connectors),
        "affected_workflows": sorted(affected_workflows),
        "affected_kpis": sorted(affected_kpis),
        "confidence_impact": max((float(mode.get("confidence_impact") or 0.0) for mode in degraded_modes), default=0.0),
        "next_action_cta": next(
            (
                str(mode.get("next_action_cta"))
                for mode in degraded_modes
                if mode.get("next_action_cta") and mode.get("next_action_cta") != "none"
            ),
            "none",
        ),
        "reasons": [
            str(mode.get("reason"))
            for mode in degraded_modes
            if mode.get("reason")
        ],
    }


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
