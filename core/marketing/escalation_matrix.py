"""CMO escalation matrix and evidence projection.

CMO-6.2 makes escalation routes explicit and machine-checkable. The defaults
are intentionally conservative: customer-facing failures, approval timeouts,
missing policy, connector failures, data-quality blockers, crisis response, and
high-risk claims route to accountable owners with SLA, fallback, notification,
and audit metadata.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from core.marketing.decision_audit import build_escalation_decision_audit

ESCALATION_TRIGGER_TYPES = (
    "approval_timeout",
    "crisis_public_response",
    "budget_threshold_exceeded",
    "connector_auth_expired",
    "connector_degraded",
    "data_mapping_blocked",
    "backfill_failed",
    "missing_policy",
    "external_write_rejected",
    "external_write_timeout_unknown",
    "high_risk_copy",
    "pricing_or_legal_claim",
    "target_account_change",
)

ESCALATION_DECISIONS = (
    "no_escalation",
    "notify_owner",
    "escalate",
    "escalate_to_legal",
    "escalate_to_finance",
    "escalate_to_admin",
    "pause_workflow",
    "require_manual_resolution",
)

CONNECTOR_AUTH_FAILURES = {"auth_expired", "expired_auth", "token_expired"}
CONNECTOR_DEGRADED_FAILURES = {
    "connector_disabled",
    "degraded",
    "insufficient_scope",
    "malformed_payload",
    "missing_scope",
    "partial_data",
    "quota_exhausted",
    "rate_limited",
    "stale_data",
    "timeout",
    "vendor_5xx",
}
BUDGET_ACTIONS = {
    "ad_budget_change",
    "budget_change",
    "create_abm_campaign",
    "increase_budget",
    "launch_abm_campaign",
    "launch_competitive_campaign",
    "mutate_ad_budget",
    "set_abm_budget",
    "spend",
    "update_ad_budget",
}
CRISIS_ACTIONS = {
    "crisis_response",
    "detect_crisis",
    "major_competitor_launch",
    "public_response",
    "publish_brand_response",
    "publish_competitive_response",
}
HIGH_RISK_ACTIONS = {"brand_claim", "claims_review", "comparative_claim", "high_risk_copy"}
PRICING_LEGAL_ACTIONS = {
    "compliance_claim",
    "legal_claim",
    "pricing_claim",
    "regulated_claim",
}
TARGET_ACCOUNT_ACTIONS = {
    "query_target_accounts",
    "sync_target_accounts",
    "target_account_change",
    "target_account_list_change",
    "update_target_accounts",
}
CUSTOMER_WRITE_ACTION_HINTS = (
    "activate",
    "add_to_drip",
    "launch",
    "mutate",
    "publish",
    "schedule",
    "send",
    "spend",
    "update_crm",
    "write",
)


@dataclass(frozen=True)
class MarketingEscalationRoute:
    trigger_type: str
    severity: str
    primary_owner_role: str
    backup_owner_role: str
    escalation_chain: tuple[str, ...]
    sla: timedelta
    notification_channels: tuple[str, ...]
    fallback_outcome: str
    audit_event_code: str
    next_action_cta: str
    decision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_type": self.trigger_type,
            "severity": self.severity,
            "primary_owner_role": self.primary_owner_role,
            "backup_owner_role": self.backup_owner_role,
            "escalation_chain": list(self.escalation_chain),
            "sla_seconds": int(self.sla.total_seconds()),
            "sla_duration": _duration_label(self.sla),
            "notification_channels": list(self.notification_channels),
            "fallback_outcome": self.fallback_outcome,
            "audit_event_code": self.audit_event_code,
            "next_action_cta": self.next_action_cta,
            "decision": self.decision,
        }


@dataclass(frozen=True)
class MarketingEscalationMatrix:
    policy_id: str
    version: str
    routes: tuple[MarketingEscalationRoute, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "routes": [route.to_dict() for route in self.routes],
        }


DEFAULT_NOTIFICATION_CHANNELS = ("in_app", "email", "slack")
DEFAULT_CHAIN = ("marketing_ops", "growth_lead", "cmo", "ceo")

DEFAULT_MARKETING_ESCALATION_MATRIX = MarketingEscalationMatrix(
    policy_id="cmo_default_escalation_matrix",
    version="2026-05-23.cmo-6.2",
    routes=(
        MarketingEscalationRoute(
            trigger_type="approval_timeout",
            severity="high",
            primary_owner_role="cmo",
            backup_owner_role="ceo",
            escalation_chain=DEFAULT_CHAIN,
            sla=timedelta(hours=1),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="pause_workflow",
            audit_event_code="cmo_escalation_approval_timeout",
            next_action_cta="review_overdue_approval",
            decision="escalate",
        ),
        MarketingEscalationRoute(
            trigger_type="crisis_public_response",
            severity="critical",
            primary_owner_role="cmo",
            backup_owner_role="ceo",
            escalation_chain=("brand_comms_lead", "cmo", "legal", "ceo"),
            sla=timedelta(minutes=30),
            notification_channels=("in_app", "email", "slack", "sms"),
            fallback_outcome="pause_workflow",
            audit_event_code="cmo_escalation_crisis_public_response",
            next_action_cta="escalate_crisis_response_to_exec",
            decision="escalate",
        ),
        MarketingEscalationRoute(
            trigger_type="budget_threshold_exceeded",
            severity="high",
            primary_owner_role="growth_lead",
            backup_owner_role="cmo",
            escalation_chain=("growth_lead", "cmo", "cfo", "ceo"),
            sla=timedelta(hours=2),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="pause_workflow",
            audit_event_code="cmo_escalation_budget_threshold_exceeded",
            next_action_cta="request_finance_budget_review",
            decision="escalate_to_finance",
        ),
        MarketingEscalationRoute(
            trigger_type="connector_auth_expired",
            severity="high",
            primary_owner_role="admin_it_owner",
            backup_owner_role="marketing_ops",
            escalation_chain=("marketing_ops", "admin_it_owner", "cmo"),
            sla=timedelta(hours=2),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="require_manual_resolution",
            audit_event_code="cmo_escalation_connector_auth_expired",
            next_action_cta="reconnect_connector",
            decision="escalate_to_admin",
        ),
        MarketingEscalationRoute(
            trigger_type="connector_degraded",
            severity="medium",
            primary_owner_role="marketing_ops",
            backup_owner_role="admin_it_owner",
            escalation_chain=("marketing_ops", "admin_it_owner", "cmo"),
            sla=timedelta(hours=4),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="continue_read_only",
            audit_event_code="cmo_escalation_connector_degraded",
            next_action_cta="review_connector_degradation",
            decision="notify_owner",
        ),
        MarketingEscalationRoute(
            trigger_type="data_mapping_blocked",
            severity="high",
            primary_owner_role="marketing_ops",
            backup_owner_role="revops_lead",
            escalation_chain=("marketing_ops", "revops_lead", "cmo"),
            sla=timedelta(hours=4),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="require_manual_resolution",
            audit_event_code="cmo_escalation_data_mapping_blocked",
            next_action_cta="resolve_mapping_blocker",
            decision="require_manual_resolution",
        ),
        MarketingEscalationRoute(
            trigger_type="backfill_failed",
            severity="high",
            primary_owner_role="marketing_ops",
            backup_owner_role="revops_lead",
            escalation_chain=("marketing_ops", "revops_lead", "admin_it_owner", "cmo"),
            sla=timedelta(hours=6),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="require_manual_resolution",
            audit_event_code="cmo_escalation_backfill_failed",
            next_action_cta="retry_or_reconcile_backfill",
            decision="require_manual_resolution",
        ),
        MarketingEscalationRoute(
            trigger_type="missing_policy",
            severity="critical",
            primary_owner_role="policy_owner",
            backup_owner_role="cmo",
            escalation_chain=("policy_owner", "legal", "cmo", "ceo"),
            sla=timedelta(hours=2),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="require_manual_resolution",
            audit_event_code="cmo_escalation_missing_policy",
            next_action_cta="configure_marketing_policy_manifest",
            decision="require_manual_resolution",
        ),
        MarketingEscalationRoute(
            trigger_type="external_write_rejected",
            severity="high",
            primary_owner_role="workflow_owner",
            backup_owner_role="cmo",
            escalation_chain=("workflow_owner", "marketing_ops", "cmo"),
            sla=timedelta(hours=1),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="pause_workflow",
            audit_event_code="cmo_escalation_external_write_rejected",
            next_action_cta="fix_and_resubmit_write",
            decision="escalate",
        ),
        MarketingEscalationRoute(
            trigger_type="external_write_timeout_unknown",
            severity="critical",
            primary_owner_role="marketing_ops",
            backup_owner_role="cmo",
            escalation_chain=("marketing_ops", "admin_it_owner", "cmo", "ceo"),
            sla=timedelta(minutes=30),
            notification_channels=("in_app", "email", "slack", "sms"),
            fallback_outcome="pause_workflow",
            audit_event_code="cmo_escalation_external_write_timeout_unknown",
            next_action_cta="manual_reconcile_before_retry",
            decision="pause_workflow",
        ),
        MarketingEscalationRoute(
            trigger_type="high_risk_copy",
            severity="high",
            primary_owner_role="legal",
            backup_owner_role="cmo",
            escalation_chain=("content_lead", "legal", "cmo"),
            sla=timedelta(hours=2),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="require_manual_resolution",
            audit_event_code="cmo_escalation_high_risk_copy",
            next_action_cta="request_legal_or_compliance_review",
            decision="escalate_to_legal",
        ),
        MarketingEscalationRoute(
            trigger_type="pricing_or_legal_claim",
            severity="critical",
            primary_owner_role="legal",
            backup_owner_role="cmo",
            escalation_chain=("content_lead", "legal", "compliance", "cmo", "ceo"),
            sla=timedelta(hours=2),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="require_manual_resolution",
            audit_event_code="cmo_escalation_pricing_or_legal_claim",
            next_action_cta="request_legal_or_compliance_review",
            decision="escalate_to_legal",
        ),
        MarketingEscalationRoute(
            trigger_type="target_account_change",
            severity="high",
            primary_owner_role="growth_lead",
            backup_owner_role="cmo",
            escalation_chain=("revops_lead", "growth_lead", "cmo"),
            sla=timedelta(hours=4),
            notification_channels=DEFAULT_NOTIFICATION_CHANNELS,
            fallback_outcome="require_manual_resolution",
            audit_event_code="cmo_escalation_target_account_change",
            next_action_cta="review_target_account_change",
            decision="escalate",
        ),
    ),
)


def default_marketing_escalation_matrix() -> dict[str, Any]:
    """Return a serializable default escalation matrix."""

    return DEFAULT_MARKETING_ESCALATION_MATRIX.to_dict()


def load_marketing_escalation_matrix(
    source: Mapping[str, Any] | None = None,
    *,
    use_default: bool = True,
) -> dict[str, Any] | None:
    """Load an escalation matrix from config or return conservative defaults."""

    if _matrix_disabled(source):
        return None
    candidate = _matrix_candidate(source)
    if candidate is None:
        return default_marketing_escalation_matrix() if use_default else None
    if not candidate or _truthy(candidate.get("disabled")):
        return None
    matrix = _normalize_matrix(candidate)
    if not matrix.get("policy_id") or not matrix.get("version"):
        return None
    return matrix


def evaluate_marketing_escalation(
    context: Mapping[str, Any] | None = None,
    *,
    matrix: Mapping[str, Any] | None = None,
    use_default: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate one escalation event/context and return route evidence."""

    ctx = context if isinstance(context, Mapping) else {}
    now = _ensure_aware(now) or datetime.now(UTC)
    trigger_type = _trigger_from_context(ctx)
    if trigger_type is None:
        return _no_escalation_decision(ctx, now)

    matrix_source = matrix if matrix is not None else ctx
    loaded = load_marketing_escalation_matrix(matrix_source, use_default=use_default)
    if loaded is None:
        return _missing_route_decision(ctx, trigger_type, now)

    route = _route_by_trigger(loaded, trigger_type)
    if route is None:
        return _missing_route_decision(ctx, trigger_type, now, loaded)

    sla_seconds = _int_or_default(route.get("sla_seconds"), 0)
    due_at = _parse_datetime(ctx.get("escalation_due_at")) or (now + timedelta(seconds=sla_seconds))
    event_id = _event_id(ctx, trigger_type)
    audit_reference = _audit_reference(
        event_id,
        trigger_type,
        ctx.get("workflow_id"),
        ctx.get("workflow_run_id") or ctx.get("run_id"),
        ctx.get("step_id"),
    )
    decision = str(route.get("decision") or "escalate")
    if decision not in ESCALATION_DECISIONS:
        decision = "escalate"
    severity = _severity(ctx.get("severity"), route.get("severity"))
    evidence = {
        "event_id": event_id,
        "event_type": trigger_type,
        "workflow_id": _string_or_none(ctx.get("workflow_id")),
        "workflow_run_id": _string_or_none(ctx.get("workflow_run_id")),
        "run_id": _string_or_none(ctx.get("run_id")),
        "step_id": _string_or_none(ctx.get("step_id")),
        "severity": severity,
        "owner_role": _string_or_none(route.get("primary_owner_role")),
        "escalation_target": _string_or_none(route.get("primary_owner_role")),
        "backup_owner_role": _string_or_none(route.get("backup_owner_role")),
        "escalation_chain": _string_list(route.get("escalation_chain")),
        "created_at": now.isoformat(),
        "due_at": due_at.isoformat(),
        "sla_seconds": sla_seconds,
        "sla_duration": route.get("sla_duration") or _duration_label(timedelta(seconds=sla_seconds)),
        "fallback_outcome": route.get("fallback_outcome"),
        "notification_channels": _string_list(route.get("notification_channels")),
        "audit_event_code": route.get("audit_event_code"),
        "audit_reference": audit_reference,
    }
    result = {
        "escalation_policy_id": loaded.get("policy_id"),
        "policy_id": loaded.get("policy_id"),
        "version": loaded.get("version"),
        "route_found": True,
        "trigger_type": trigger_type,
        "event_id": event_id,
        "event_type": trigger_type,
        "severity": severity,
        "decision": decision,
        "reason": _string_or_none(ctx.get("reason")) or f"Escalation route matched trigger {trigger_type}.",
        "primary_owner_role": route.get("primary_owner_role"),
        "backup_owner_role": route.get("backup_owner_role"),
        "owner_role": route.get("primary_owner_role"),
        "escalation_target": route.get("primary_owner_role"),
        "escalation_chain": _string_list(route.get("escalation_chain")),
        "sla_seconds": sla_seconds,
        "sla_duration": route.get("sla_duration") or _duration_label(timedelta(seconds=sla_seconds)),
        "due_at": due_at.isoformat(),
        "fallback_outcome": route.get("fallback_outcome"),
        "notification_channels": _string_list(route.get("notification_channels")),
        "audit_event_code": route.get("audit_event_code"),
        "next_action_cta": route.get("next_action_cta") or "review_escalation",
        "workflow_id": _string_or_none(ctx.get("workflow_id")),
        "workflow_run_id": _string_or_none(ctx.get("workflow_run_id")),
        "run_id": _string_or_none(ctx.get("run_id")),
        "step_id": _string_or_none(ctx.get("step_id")),
        "audit_reference": audit_reference,
        "evidence": evidence,
    }
    audit_package = build_escalation_decision_audit(result, now=now)
    result["decision_audit"] = audit_package
    result["decision_audit_ref"] = audit_package["audit_reference"]
    evidence["decision_audit_ref"] = audit_package["audit_reference"]
    return result


def escalation_triggers_for_action(
    action: Any,
    context: Mapping[str, Any] | None = None,
    *,
    external_write_required: bool = False,
) -> list[str]:
    """Infer escalation trigger types for a workflow/action context."""

    ctx = context if isinstance(context, Mapping) else {}
    explicit = _explicit_triggers(ctx)
    if explicit:
        return explicit

    triggers: list[str] = []
    normalized_action = _normalize_key(action)
    policy_decision = ctx.get("marketing_policy_decision") or ctx.get("policy_decision")
    if isinstance(policy_decision, Mapping):
        decision = _normalize_key(policy_decision.get("decision"))
        if decision == "missing_policy":
            _append_unique(triggers, "missing_policy")
        role = _normalize_key(
            policy_decision.get("required_escalation_role")
            or policy_decision.get("required_approver_role")
        )
        if role in {"legal", "compliance"}:
            _append_unique(triggers, "pricing_or_legal_claim")
        if decision == "requires_escalation" and not triggers:
            _append_unique(triggers, "high_risk_copy")

    failure_class = _normalize_key(ctx.get("failure_class") or ctx.get("connector_failure_class"))
    if failure_class in CONNECTOR_AUTH_FAILURES:
        _append_unique(triggers, "connector_auth_expired")
    elif failure_class in CONNECTOR_DEGRADED_FAILURES:
        _append_unique(triggers, "connector_degraded")

    final_state = _normalize_key(
        ctx.get("final_state")
        or ctx.get("external_write_state")
        or ctx.get("write_state")
        or ctx.get("status")
    )
    if final_state == "rejected":
        _append_unique(triggers, "external_write_rejected")
    if final_state == "timeout_unknown":
        _append_unique(triggers, "external_write_timeout_unknown")

    mapping_status = _normalize_key(ctx.get("mapping_status") or ctx.get("field_mapping_status"))
    if mapping_status in {"blocked", "invalid", "unmapped"} and ctx.get("missing_blocks") is not False:
        _append_unique(triggers, "data_mapping_blocked")

    backfill_status = _normalize_key(ctx.get("backfill_status") or ctx.get("status"))
    if backfill_status in {"blocked", "failed"} and _truthy(ctx.get("is_backfill")):
        _append_unique(triggers, "backfill_failed")

    if _truthy(ctx.get("missing_policy")) or _truthy(ctx.get("marketing_policy_manifest_disabled")):
        _append_unique(triggers, "missing_policy")
    if (
        normalized_action in CRISIS_ACTIONS
        or _truthy(ctx.get("crisis_response"))
        or _truthy(ctx.get("public_response"))
    ):
        _append_unique(triggers, "crisis_public_response")
    if normalized_action in PRICING_LEGAL_ACTIONS or any(
        _truthy(ctx.get(field))
        for field in (
            "comparative_claim",
            "competitor_mention",
            "compliance_claim",
            "legal_claim",
            "pricing_claim",
            "regulated_claim",
        )
    ):
        _append_unique(triggers, "pricing_or_legal_claim")
    elif normalized_action in HIGH_RISK_ACTIONS or any(
        _truthy(ctx.get(field))
        for field in ("brand_claim", "claims_review", "high_risk", "high_risk_copy")
    ):
        _append_unique(triggers, "high_risk_copy")
    if normalized_action in BUDGET_ACTIONS or _truthy(ctx.get("budget_threshold_exceeded")):
        _append_unique(triggers, "budget_threshold_exceeded")
    if normalized_action in TARGET_ACCOUNT_ACTIONS or _truthy(ctx.get("target_account_change")):
        _append_unique(triggers, "target_account_change")

    approval_sensitive = any(
        _truthy(ctx.get(field))
        for field in (
            "approval_required",
            "approval_sensitive",
            "hitl_required",
            "requires_approval",
            "requires_human_approval",
        )
    )
    if approval_sensitive or external_write_required or _action_looks_customer_write(normalized_action):
        _append_unique(triggers, "approval_timeout")
    if external_write_required or _action_looks_customer_write(normalized_action):
        _append_unique(triggers, "external_write_rejected")
        _append_unique(triggers, "external_write_timeout_unknown")

    return triggers


def has_escalation_route_for_step(
    step: Mapping[str, Any],
    *,
    workflow_definition: Mapping[str, Any] | None = None,
    action: Any = None,
    matrix: Mapping[str, Any] | None = None,
    external_write_required: bool = False,
) -> bool:
    """Return true when every inferred step trigger has an escalation route."""

    return not missing_escalation_triggers_for_step(
        step,
        workflow_definition=workflow_definition,
        action=action,
        matrix=matrix,
        external_write_required=external_write_required,
    )


def missing_escalation_triggers_for_step(
    step: Mapping[str, Any],
    *,
    workflow_definition: Mapping[str, Any] | None = None,
    action: Any = None,
    matrix: Mapping[str, Any] | None = None,
    external_write_required: bool = False,
) -> list[str]:
    """Return inferred escalation triggers that have no configured route."""

    source: dict[str, Any] = {
        **(dict(workflow_definition) if isinstance(workflow_definition, Mapping) else {}),
        **(dict(step) if isinstance(step, Mapping) else {}),
    }
    action_value = action or source.get("action") or source.get("approval_action")
    triggers = escalation_triggers_for_action(
        action_value,
        source,
        external_write_required=external_write_required,
    )
    missing: list[str] = []
    for trigger in triggers:
        decision = evaluate_marketing_escalation(
            {**source, "trigger_type": trigger},
            matrix=matrix,
            use_default=matrix is None,
        )
        if not decision.get("route_found"):
            missing.append(trigger)
    return missing


def build_workflow_escalation_status(
    workflow_key: str,
    actions: Iterable[Any],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project escalation-route readiness for one CMO workflow."""

    payload = payload if isinstance(payload, Mapping) else {}
    triggers: list[str] = []
    for action in actions:
        for trigger in escalation_triggers_for_action(
            action,
            payload,
            external_write_required=True,
        ):
            _append_unique(triggers, trigger)
    for trigger in _explicit_triggers(payload):
        _append_unique(triggers, trigger)

    decisions = [
        evaluate_marketing_escalation(
            {
                **dict(payload),
                "workflow_id": workflow_key,
                "trigger_type": trigger,
            },
            use_default=True,
        )
        for trigger in triggers
    ]
    missing = [
        str(decision.get("trigger_type"))
        for decision in decisions
        if not decision.get("route_found")
    ]
    if not triggers:
        status = "not_required"
        next_action = "none"
    elif missing:
        status = "missing_route"
        next_action = "configure_escalation_matrix"
    else:
        status = "ready"
        next_action = "none"

    return {
        "workflow_key": _normalize_key(workflow_key),
        "status": status,
        "required_triggers": triggers,
        "covered_triggers": [
            str(decision.get("trigger_type"))
            for decision in decisions
            if decision.get("route_found")
        ],
        "missing_route_triggers": missing,
        "decisions": decisions,
        "next_action_cta": next_action,
    }


def build_marketing_escalation_projection(
    sources: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Build a KPI/API projection for the active escalation matrix."""

    matrix = None
    for source in sources or []:
        config = _config_dict(source)
        if _matrix_disabled(config):
            return {
                "marketing_escalation_matrix": None,
                "marketing_escalation_summary": summarize_marketing_escalation_matrix(
                    {"marketing_escalation_matrix_disabled": True}
                ),
            }
        candidate = load_marketing_escalation_matrix(config, use_default=False)
        if candidate is not None:
            matrix = candidate
            break
    if matrix is None:
        matrix = default_marketing_escalation_matrix()
    return {
        "marketing_escalation_matrix": matrix,
        "marketing_escalation_summary": summarize_marketing_escalation_matrix(matrix),
    }


def summarize_marketing_escalation_matrix(
    matrix: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    loaded = load_marketing_escalation_matrix(matrix, use_default=True)
    if loaded is None:
        return {
            "status": "missing_route",
            "policy_id": None,
            "version": None,
            "route_count": 0,
            "missing_trigger_types": list(ESCALATION_TRIGGER_TYPES),
            "next_action_cta": "configure_escalation_matrix",
        }
    routes = [
        route
        for route in loaded.get("routes") or []
        if isinstance(route, Mapping)
    ]
    covered = {_normalize_key(route.get("trigger_type")) for route in routes}
    missing = [trigger for trigger in ESCALATION_TRIGGER_TYPES if trigger not in covered]
    decisions = dict.fromkeys(ESCALATION_DECISIONS, 0)
    for route in routes:
        decision = str(route.get("decision") or "")
        if decision in decisions:
            decisions[decision] += 1
    return {
        "status": "ready" if not missing else "missing_route",
        "policy_id": loaded.get("policy_id"),
        "version": loaded.get("version"),
        "route_count": len(routes),
        "covered_trigger_types": sorted(covered),
        "missing_trigger_types": missing,
        "next_action_cta": "none" if not missing else "configure_escalation_matrix",
        **decisions,
    }


def _route_by_trigger(
    matrix: Mapping[str, Any],
    trigger_type: str,
) -> dict[str, Any] | None:
    normalized = _normalize_key(trigger_type)
    for route in matrix.get("routes") or []:
        if isinstance(route, Mapping) and _normalize_key(route.get("trigger_type")) == normalized:
            return dict(route)
    return None


def _normalize_matrix(candidate: Mapping[str, Any]) -> dict[str, Any]:
    routes = candidate.get("routes") or candidate.get("escalation_routes") or []
    normalized_routes: list[dict[str, Any]] = []
    if isinstance(routes, Mapping):
        iterable = []
        for trigger_type, route in routes.items():
            if isinstance(route, Mapping):
                iterable.append({"trigger_type": trigger_type, **dict(route)})
    elif isinstance(routes, (list, tuple)):
        iterable = [route for route in routes if isinstance(route, Mapping)]
    else:
        iterable = []

    for route in iterable:
        trigger = _normalize_key(route.get("trigger_type"))
        if trigger not in ESCALATION_TRIGGER_TYPES:
            continue
        sla = _sla_from_route(route)
        decision = _normalize_key(route.get("decision") or "escalate")
        if decision not in ESCALATION_DECISIONS:
            decision = "escalate"
        normalized_routes.append(
            {
                "trigger_type": trigger,
                "severity": _severity(route.get("severity"), "high"),
                "primary_owner_role": str(route.get("primary_owner_role") or "cmo"),
                "backup_owner_role": str(route.get("backup_owner_role") or "ceo"),
                "escalation_chain": _string_list(route.get("escalation_chain")) or list(DEFAULT_CHAIN),
                "sla_seconds": int(sla.total_seconds()),
                "sla_duration": _duration_label(sla),
                "notification_channels": (
                    _string_list(route.get("notification_channels"))
                    or list(DEFAULT_NOTIFICATION_CHANNELS)
                ),
                "fallback_outcome": str(route.get("fallback_outcome") or "require_manual_resolution"),
                "audit_event_code": str(route.get("audit_event_code") or f"cmo_escalation_{trigger}"),
                "next_action_cta": str(route.get("next_action_cta") or "review_escalation"),
                "decision": decision,
            }
        )
    return {
        "policy_id": str(candidate.get("policy_id") or "cmo_custom_escalation_matrix"),
        "version": str(candidate.get("version") or "custom"),
        "routes": normalized_routes,
    }


def _matrix_candidate(source: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(source, Mapping):
        return None
    for key in (
        "marketing_escalation_matrix",
        "cmo_escalation_matrix",
        "escalation_matrix",
    ):
        value = source.get(key)
        if isinstance(value, Mapping):
            return value
    if source.get("policy_id") and source.get("version") and isinstance(source.get("routes"), list):
        return source
    return None


def _matrix_disabled(source: Mapping[str, Any] | None) -> bool:
    if not isinstance(source, Mapping):
        return False
    return any(
        _truthy(source.get(key))
        for key in (
            "marketing_escalation_matrix_disabled",
            "cmo_escalation_matrix_disabled",
            "escalation_matrix_disabled",
        )
    )


def _explicit_triggers(context: Mapping[str, Any]) -> list[str]:
    raw = (
        context.get("trigger_type")
        or context.get("escalation_trigger_type")
        or context.get("escalation_triggers")
        or context.get("escalation_trigger_types")
    )
    values = _string_list(raw)
    return [value for value in values if value in ESCALATION_TRIGGER_TYPES]


def _trigger_from_context(context: Mapping[str, Any]) -> str | None:
    triggers = escalation_triggers_for_action(
        context.get("action") or context.get("blocked_action"),
        context,
        external_write_required=_truthy(
            context.get("external_write_required")
            or context.get("requires_external_write")
        ),
    )
    return triggers[0] if triggers else None


def _missing_route_decision(
    context: Mapping[str, Any],
    trigger_type: str,
    now: datetime,
    matrix: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    event_id = _event_id(context, trigger_type)
    audit_reference = _audit_reference(
        event_id,
        trigger_type,
        context.get("workflow_id"),
        context.get("workflow_run_id") or context.get("run_id"),
        context.get("step_id"),
    )
    evidence = {
        "event_id": event_id,
        "event_type": trigger_type,
        "workflow_id": _string_or_none(context.get("workflow_id")),
        "workflow_run_id": _string_or_none(context.get("workflow_run_id")),
        "run_id": _string_or_none(context.get("run_id")),
        "step_id": _string_or_none(context.get("step_id")),
        "severity": "critical",
        "owner_role": "cmo",
        "escalation_target": "cmo",
        "backup_owner_role": "ceo",
        "escalation_chain": ["cmo", "ceo"],
        "created_at": now.isoformat(),
        "due_at": now.isoformat(),
        "sla_seconds": 0,
        "sla_duration": "0s",
        "fallback_outcome": "require_manual_resolution",
        "notification_channels": [],
        "audit_event_code": "cmo_escalation_route_missing",
        "audit_reference": audit_reference,
    }
    result = {
        "escalation_policy_id": (matrix or {}).get("policy_id"),
        "policy_id": (matrix or {}).get("policy_id"),
        "version": (matrix or {}).get("version"),
        "route_found": False,
        "trigger_type": trigger_type,
        "event_id": event_id,
        "event_type": trigger_type,
        "severity": "critical",
        "decision": "require_manual_resolution",
        "reason": f"No CMO escalation route is configured for trigger {trigger_type}.",
        "primary_owner_role": None,
        "backup_owner_role": None,
        "owner_role": "cmo",
        "escalation_target": "cmo",
        "escalation_chain": ["cmo", "ceo"],
        "sla_seconds": 0,
        "sla_duration": "0s",
        "due_at": now.isoformat(),
        "fallback_outcome": "require_manual_resolution",
        "notification_channels": [],
        "audit_event_code": "cmo_escalation_route_missing",
        "next_action_cta": "configure_escalation_matrix",
        "workflow_id": _string_or_none(context.get("workflow_id")),
        "workflow_run_id": _string_or_none(context.get("workflow_run_id")),
        "run_id": _string_or_none(context.get("run_id")),
        "step_id": _string_or_none(context.get("step_id")),
        "audit_reference": audit_reference,
        "evidence": evidence,
    }
    audit_package = build_escalation_decision_audit(result, now=now)
    result["decision_audit"] = audit_package
    result["decision_audit_ref"] = audit_package["audit_reference"]
    evidence["decision_audit_ref"] = audit_package["audit_reference"]
    return result


def _no_escalation_decision(
    context: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    return {
        "escalation_policy_id": None,
        "policy_id": None,
        "version": None,
        "route_found": False,
        "trigger_type": None,
        "event_id": _event_id(context, "no_escalation"),
        "event_type": "no_escalation",
        "severity": "none",
        "decision": "no_escalation",
        "reason": "No escalation-sensitive trigger was found.",
        "primary_owner_role": None,
        "backup_owner_role": None,
        "owner_role": None,
        "escalation_target": None,
        "escalation_chain": [],
        "sla_seconds": 0,
        "sla_duration": "0s",
        "due_at": now.isoformat(),
        "fallback_outcome": "none",
        "notification_channels": [],
        "audit_event_code": None,
        "next_action_cta": "none",
        "workflow_id": _string_or_none(context.get("workflow_id")),
        "workflow_run_id": _string_or_none(context.get("workflow_run_id")),
        "run_id": _string_or_none(context.get("run_id")),
        "step_id": _string_or_none(context.get("step_id")),
        "audit_reference": None,
        "evidence": None,
    }


def _event_id(context: Mapping[str, Any], trigger_type: str) -> str:
    explicit = _string_or_none(context.get("event_id") or context.get("escalation_event_id"))
    if explicit:
        return explicit
    source = "|".join(
        str(item or "")
        for item in (
            trigger_type,
            context.get("workflow_id"),
            context.get("workflow_run_id") or context.get("run_id"),
            context.get("step_id"),
            context.get("action") or context.get("blocked_action"),
        )
    )
    return f"mkt_esc_evt_{hashlib.sha256(source.encode()).hexdigest()[:20]}"


def _audit_reference(*parts: Any) -> str:
    source = "|".join(str(part or "") for part in parts)
    return f"mkt_escalation_{hashlib.sha256(source.encode()).hexdigest()[:20]}"


def _sla_from_route(route: Mapping[str, Any]) -> timedelta:
    if route.get("sla_seconds") is not None:
        return timedelta(seconds=max(_int_or_default(route.get("sla_seconds"), 0), 0))
    if route.get("sla_minutes") is not None:
        return timedelta(minutes=max(_int_or_default(route.get("sla_minutes"), 0), 0))
    if route.get("sla_hours") is not None:
        return timedelta(hours=max(_float_or_default(route.get("sla_hours"), 0.0), 0.0))
    return _parse_duration(route.get("sla_duration") or route.get("sla")) or timedelta(hours=1)


def _parse_duration(value: Any) -> timedelta | None:
    if isinstance(value, timedelta):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text.endswith("minutes"):
        return timedelta(minutes=_float_or_default(text[:-7].strip(), 0.0))
    if text.endswith("minute"):
        return timedelta(minutes=_float_or_default(text[:-6].strip(), 0.0))
    if text.endswith("hours"):
        return timedelta(hours=_float_or_default(text[:-5].strip(), 0.0))
    if text.endswith("hour"):
        return timedelta(hours=_float_or_default(text[:-4].strip(), 0.0))
    suffix = text[-1]
    amount = _float_or_default(text[:-1], -1.0)
    if amount < 0:
        return None
    if suffix == "s":
        return timedelta(seconds=amount)
    if suffix == "m":
        return timedelta(minutes=amount)
    if suffix == "h":
        return timedelta(hours=amount)
    if suffix == "d":
        return timedelta(days=amount)
    return None


def _duration_label(value: timedelta) -> str:
    seconds = int(value.total_seconds())
    if seconds % 3600 == 0 and seconds:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0 and seconds:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _config_dict(config: Any | None) -> dict[str, Any]:
    value = getattr(config, "config", None)
    return value if isinstance(value, dict) else {}


def _action_looks_customer_write(action: str) -> bool:
    return any(hint in action for hint in CUSTOMER_WRITE_ACTION_HINTS)


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _severity(requested: Any, fallback: Any) -> str:
    value = _normalize_key(requested)
    return value if value in {"low", "medium", "high", "critical"} else _normalize_key(fallback) or "high"


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [_normalize_key(item) for item in value.replace(",", " ").split() if _normalize_key(item)]
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return [_normalize_key(item) for item in value if _normalize_key(item)]
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


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
