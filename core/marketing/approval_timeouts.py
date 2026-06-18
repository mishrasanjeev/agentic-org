"""CMO approval timeout policy and audit projections.

CMO-5.4 keeps approval-sensitive marketing work from hanging indefinitely or
continuing unsafely after an approval SLA expires. The module is intentionally
vendor-neutral: it evaluates approval records and workflow metadata, emits
auditable timeout decisions, and lets callers fail closed before customer-
facing writes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from core.marketing.decision_audit import build_approval_timeout_decision_audit
from core.marketing.escalation_matrix import evaluate_marketing_escalation
from core.marketing.policy_manifest import evaluate_marketing_policy

APPROVAL_TIMEOUT_OUTCOMES = (
    "auto_cancel",
    "auto_escalate",
    "continue_read_only",
    "pause_workflow",
    "require_manual_resolution",
)

APPROVAL_SENSITIVE_TYPES = (
    "ad_campaign_launch",
    "ad_budget_change",
    "email_send",
    "content_publish",
    "landing_page_change",
    "target_account_list_change",
    "crisis_public_response",
    "social_post_target_behavior",
    "high_risk_copy_or_pricing_claims",
    "crm_lifecycle_update",
    "crm_segment_update",
    "crm_bulk_update",
)

ACTION_APPROVAL_TYPES: dict[str, str] = {
    "activate_campaign": "ad_campaign_launch",
    "create_abm_campaign": "ad_campaign_launch",
    "create_campaign": "ad_campaign_launch",
    "create_linkedin_campaign": "ad_campaign_launch",
    "launch_abm_campaign": "ad_campaign_launch",
    "launch_campaign": "ad_campaign_launch",
    "setup_google_campaign": "ad_campaign_launch",
    "setup_meta_campaign": "ad_campaign_launch",
    "mutate_ad_budget": "ad_budget_change",
    "pause_campaign": "ad_budget_change",
    "set_abm_budget": "ad_budget_change",
    "spend": "ad_budget_change",
    "update_ad_budget": "ad_budget_change",
    "add_to_drip": "email_send",
    "send": "email_send",
    "send_email": "email_send",
    "send_to_segment": "email_send",
    "send_winner": "email_send",
    "start_nurture_sequence": "email_send",
    "publish": "content_publish",
    "publish_seo_change": "landing_page_change",
    "publish_post": "social_post_target_behavior",
    "publish_to_wordpress": "content_publish",
    "schedule_content": "content_publish",
    "apply_redirect": "landing_page_change",
    "submit_url_to_index": "landing_page_change",
    "update_canonical_tag": "landing_page_change",
    "update_landing_page": "landing_page_change",
    "update_page_metadata": "landing_page_change",
    "update_robots_txt": "landing_page_change",
    "update_sitemap": "landing_page_change",
    "publish_landing_page": "landing_page_change",
    "landing_page_change": "landing_page_change",
    "change_target_accounts": "target_account_list_change",
    "query_target_accounts": "target_account_list_change",
    "sync_target_accounts": "target_account_list_change",
    "target_account_list_change": "target_account_list_change",
    "update_target_accounts": "target_account_list_change",
    "promote_lifecycle_stage": "crm_lifecycle_update",
    "update_lifecycle_stage": "crm_lifecycle_update",
    "update_lead_scores": "crm_lifecycle_update",
    "change_segment_membership": "crm_segment_update",
    "update_segment_membership": "crm_segment_update",
    "bulk_crm_update": "crm_bulk_update",
    "update_crm": "crm_bulk_update",
    "crisis_response": "crisis_public_response",
    "detect_crisis": "crisis_public_response",
    "publish_brand_response": "crisis_public_response",
    "publish_competitive_response": "crisis_public_response",
    "public_response": "crisis_public_response",
    "schedule_campaign_posts": "social_post_target_behavior",
    "schedule_content_promotion": "social_post_target_behavior",
    "schedule_post": "social_post_target_behavior",
    "reply_to_mention": "social_post_target_behavior",
    "social_post_target_behavior": "social_post_target_behavior",
    "brand_claim": "high_risk_copy_or_pricing_claims",
    "claims_review": "high_risk_copy_or_pricing_claims",
    "comparative_claim": "high_risk_copy_or_pricing_claims",
    "high_risk_copy": "high_risk_copy_or_pricing_claims",
    "legal_claim": "high_risk_copy_or_pricing_claims",
    "pricing_claim": "high_risk_copy_or_pricing_claims",
    "launch_competitive_campaign": "ad_campaign_launch",
}


@dataclass(frozen=True)
class ApprovalTimeoutPolicy:
    approval_type: str
    actions: tuple[str, ...]
    default_sla: timedelta
    escalation_role: str
    timeout_outcome: str
    external_writes_allowed_after_timeout: bool
    notification_event_code: str
    audit_event_code: str
    safe_fallback_cta: str
    safe_fallback_message: str
    preapproved_after_timeout: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_type": self.approval_type,
            "actions": list(self.actions),
            "default_sla_seconds": int(self.default_sla.total_seconds()),
            "default_sla_hours": self.default_sla.total_seconds() / 3600,
            "escalation_role": self.escalation_role,
            "timeout_outcome": self.timeout_outcome,
            "external_writes_allowed_after_timeout": self.external_writes_allowed_after_timeout,
            "preapproved_after_timeout": self.preapproved_after_timeout,
            "notification_event_code": self.notification_event_code,
            "audit_event_code": self.audit_event_code,
            "safe_fallback_cta": self.safe_fallback_cta,
            "safe_fallback_message": self.safe_fallback_message,
        }


DEFAULT_APPROVAL_TIMEOUT_POLICIES: dict[str, ApprovalTimeoutPolicy] = {
    "ad_campaign_launch": ApprovalTimeoutPolicy(
        approval_type="ad_campaign_launch",
        actions=(
            "activate_campaign",
            "create_abm_campaign",
            "create_campaign",
            "launch_abm_campaign",
            "launch_campaign",
            "launch_competitive_campaign",
        ),
        default_sla=timedelta(hours=4),
        escalation_role="cmo",
        timeout_outcome="auto_cancel",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_campaign_launch",
        audit_event_code="cmo_approval_timeout_auto_cancel",
        safe_fallback_cta="restart_campaign_launch_approval",
        safe_fallback_message="Campaign launch was cancelled because approval expired.",
    ),
    "ad_budget_change": ApprovalTimeoutPolicy(
        approval_type="ad_budget_change",
        actions=("mutate_ad_budget", "pause_campaign", "set_abm_budget", "update_ad_budget"),
        default_sla=timedelta(hours=2),
        escalation_role="cmo",
        timeout_outcome="auto_escalate",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_ad_budget_change",
        audit_event_code="cmo_approval_timeout_auto_escalate",
        safe_fallback_cta="review_budget_change_escalation",
        safe_fallback_message="Budget change was escalated and external writes remain blocked.",
    ),
    "email_send": ApprovalTimeoutPolicy(
        approval_type="email_send",
        actions=("send", "send_email", "send_to_segment", "send_winner"),
        default_sla=timedelta(hours=2),
        escalation_role="cmo",
        timeout_outcome="pause_workflow",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_email_send",
        audit_event_code="cmo_approval_timeout_pause_workflow",
        safe_fallback_cta="reschedule_or_cancel_email",
        safe_fallback_message="Email send was paused because approval expired.",
    ),
    "content_publish": ApprovalTimeoutPolicy(
        approval_type="content_publish",
        actions=("publish", "publish_to_wordpress", "schedule_content"),
        default_sla=timedelta(hours=4),
        escalation_role="content_lead",
        timeout_outcome="pause_workflow",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_content_publish",
        audit_event_code="cmo_approval_timeout_pause_workflow",
        safe_fallback_cta="review_content_publish_approval",
        safe_fallback_message="Content publishing was paused because approval expired.",
    ),
    "landing_page_change": ApprovalTimeoutPolicy(
        approval_type="landing_page_change",
        actions=(
            "apply_redirect",
            "landing_page_change",
            "publish_landing_page",
            "publish_seo_change",
            "submit_url_to_index",
            "update_canonical_tag",
            "update_landing_page",
            "update_page_metadata",
            "update_robots_txt",
            "update_sitemap",
        ),
        default_sla=timedelta(hours=4),
        escalation_role="growth_lead",
        timeout_outcome="require_manual_resolution",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_landing_page_change",
        audit_event_code="cmo_approval_timeout_manual_resolution",
        safe_fallback_cta="resolve_landing_page_change_manually",
        safe_fallback_message="Landing-page change is blocked until a human resolves the approval.",
    ),
    "target_account_list_change": ApprovalTimeoutPolicy(
        approval_type="target_account_list_change",
        actions=(
            "query_target_accounts",
            "sync_target_accounts",
            "target_account_list_change",
            "update_target_accounts",
        ),
        default_sla=timedelta(hours=8),
        escalation_role="revops_lead",
        timeout_outcome="require_manual_resolution",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_target_accounts",
        audit_event_code="cmo_approval_timeout_manual_resolution",
        safe_fallback_cta="resolve_target_account_list_change",
        safe_fallback_message="Target account list change is blocked until a human resolves the approval.",
    ),
    "crisis_public_response": ApprovalTimeoutPolicy(
        approval_type="crisis_public_response",
        actions=(
            "crisis_response",
            "detect_crisis",
            "public_response",
            "publish_brand_response",
            "publish_competitive_response",
        ),
        default_sla=timedelta(minutes=30),
        escalation_role="ceo",
        timeout_outcome="auto_escalate",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_crisis_response",
        audit_event_code="cmo_approval_timeout_auto_escalate",
        safe_fallback_cta="escalate_crisis_response_to_exec",
        safe_fallback_message="Public crisis response escalated; no external response is posted automatically.",
    ),
    "social_post_target_behavior": ApprovalTimeoutPolicy(
        approval_type="social_post_target_behavior",
        actions=(
            "publish_post",
            "reply_to_mention",
            "schedule_campaign_posts",
            "schedule_content_promotion",
            "schedule_post",
        ),
        default_sla=timedelta(hours=1),
        escalation_role="cmo",
        timeout_outcome="pause_workflow",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_social_post",
        audit_event_code="cmo_approval_timeout_pause_workflow",
        safe_fallback_cta="review_social_post_targeting",
        safe_fallback_message="Social post targeting is paused until approval is resolved.",
    ),
    "high_risk_copy_or_pricing_claims": ApprovalTimeoutPolicy(
        approval_type="high_risk_copy_or_pricing_claims",
        actions=("brand_claim", "claims_review", "comparative_claim", "high_risk_copy", "pricing_claim"),
        default_sla=timedelta(hours=2),
        escalation_role="legal",
        timeout_outcome="require_manual_resolution",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_high_risk_claim",
        audit_event_code="cmo_approval_timeout_manual_resolution",
        safe_fallback_cta="resolve_high_risk_claim_review",
        safe_fallback_message="High-risk copy, pricing, or legal claim is blocked until human resolution.",
    ),
    "crm_lifecycle_update": ApprovalTimeoutPolicy(
        approval_type="crm_lifecycle_update",
        actions=(
            "promote_lifecycle_stage",
            "update_lead_scores",
            "update_lifecycle_stage",
        ),
        default_sla=timedelta(hours=8),
        escalation_role="revops_lead",
        timeout_outcome="pause_workflow",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_crm_lifecycle_update",
        audit_event_code="cmo_approval_timeout_pause_workflow",
        safe_fallback_cta="review_crm_lifecycle_update_approval",
        safe_fallback_message="CRM lifecycle/score update is paused until approval is resolved.",
    ),
    "crm_segment_update": ApprovalTimeoutPolicy(
        approval_type="crm_segment_update",
        actions=(
            "change_segment_membership",
            "update_segment_membership",
        ),
        default_sla=timedelta(hours=8),
        escalation_role="revops_lead",
        timeout_outcome="pause_workflow",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_crm_segment_update",
        audit_event_code="cmo_approval_timeout_pause_workflow",
        safe_fallback_cta="review_crm_segment_update_approval",
        safe_fallback_message="CRM segment/list change is paused until approval is resolved.",
    ),
    "crm_bulk_update": ApprovalTimeoutPolicy(
        approval_type="crm_bulk_update",
        actions=(
            "bulk_crm_update",
            "update_crm",
        ),
        default_sla=timedelta(hours=8),
        escalation_role="revops_lead",
        timeout_outcome="require_manual_resolution",
        external_writes_allowed_after_timeout=False,
        notification_event_code="cmo_approval_timeout_crm_bulk_update",
        audit_event_code="cmo_approval_timeout_manual_resolution",
        safe_fallback_cta="resolve_crm_bulk_update",
        safe_fallback_message="Bulk CRM update is blocked until a human resolves the approval.",
    ),
}


def approval_type_for_action(action: Any, context: dict[str, Any] | None = None) -> str | None:
    """Return the approval type implied by a marketing action or flags."""

    context = context if isinstance(context, dict) else {}
    explicit = _normalize_key(
        context.get("approval_type")
        or context.get("approval_timeout_type")
        or context.get("approval_timeout_policy_type")
    )
    if explicit:
        return explicit

    if any(
        _truthy(context.get(field))
        for field in (
            "brand_claim",
            "claims_review",
            "claims_review_required",
            "high_risk",
            "high_risk_copy",
            "legal_claim",
            "legal_review_required",
            "pricing_claim",
        )
    ):
        return "high_risk_copy_or_pricing_claims"
    if _truthy(context.get("crisis_response")) or _truthy(context.get("public_response")):
        return "crisis_public_response"

    normalized_action = _normalize_key(action)
    return ACTION_APPROVAL_TYPES.get(normalized_action)


def requires_approval_timeout_policy(action: Any, context: dict[str, Any] | None = None) -> bool:
    """Return true when an action needs timeout policy before production use."""

    context = context if isinstance(context, dict) else {}
    if context.get("approval_sensitive") is False:
        return False
    if approval_type_for_action(action, context):
        return True
    return any(
        _truthy(context.get(field))
        for field in (
            "approval_required",
            "approval_sensitive",
            "hitl_required",
            "requires_approval",
            "requires_human_approval",
        )
    )


def timeout_policy_for_action(
    action: Any,
    source: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    approval_type = approval_type_for_action(action, source)
    if not approval_type:
        return None
    return timeout_policy_for_approval_type(approval_type, source)


def timeout_policy_for_approval_type(
    approval_type: Any,
    source: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_type = _normalize_key(approval_type)
    if not normalized_type:
        return None

    source = source if isinstance(source, dict) else {}
    if _truthy(source.get("approval_timeout_policy_disabled")):
        return None

    override = _policy_override(normalized_type, source)
    if isinstance(override, dict) and _truthy(override.get("disabled")):
        return None

    base = DEFAULT_APPROVAL_TIMEOUT_POLICIES.get(normalized_type)
    if base is None and not isinstance(override, dict):
        return None

    policy = base.to_dict() if base is not None else _minimal_policy(normalized_type)
    if isinstance(override, dict):
        policy = _merge_policy_override(policy, override)
    return policy


def has_timeout_policy_for_step(
    step: dict[str, Any],
    *,
    workflow_definition: dict[str, Any] | None = None,
) -> bool:
    """Return true when a step has default or explicit timeout policy."""

    action = step.get("action") or step.get("approval_action")
    if not requires_approval_timeout_policy(action, step):
        return True
    source = {**(workflow_definition or {}), **step}
    return timeout_policy_for_action(action, source) is not None


def build_workflow_approval_timeout_status(
    workflow_key: str,
    actions: Iterable[Any],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project timeout-policy readiness for one CMO workflow."""

    payload = payload if isinstance(payload, dict) else {}
    required_actions = _unique(
        [_normalize_key(action) for action in actions]
        + [_normalize_key(action) for action in _list_from_value(payload.get("approval_sensitive_actions"))]
    )
    required_actions = [action for action in required_actions if requires_approval_timeout_policy(action, payload)]

    policies: list[dict[str, Any]] = []
    covered_actions: list[str] = []
    missing_actions: list[str] = []
    for action in required_actions:
        policy = timeout_policy_for_action(action, payload)
        if policy is None:
            missing_actions.append(action)
            continue
        policies.append(policy)
        covered_actions.append(action)

    if not required_actions:
        status = "not_required"
        next_action = "none"
    elif missing_actions:
        status = "missing_policy"
        next_action = "configure_approval_timeout_policy"
    else:
        status = "ready"
        next_action = "none"

    return {
        "workflow_key": _normalize_key(workflow_key),
        "status": status,
        "required_actions": required_actions,
        "covered_actions": covered_actions,
        "missing_policy_actions": missing_actions,
        "policies": _dedupe_policies(policies),
        "external_writes_allowed_after_timeout": any(
            bool(policy.get("external_writes_allowed_after_timeout"))
            and bool(policy.get("preapproved_after_timeout"))
            for policy in policies
        ),
        "next_action_cta": next_action,
    }


def evaluate_approval_timeout(
    approval: dict[str, Any],
    *,
    policy_source: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate one pending approval against CMO timeout policy."""

    now = _ensure_aware(now) or datetime.now(UTC)
    source = {**(policy_source or {}), **(approval if isinstance(approval, dict) else {})}
    action = source.get("action") or source.get("approval_action") or source.get("blocked_action")
    approval_type = _normalize_key(source.get("approval_type")) or approval_type_for_action(action, source)
    policy = timeout_policy_for_approval_type(approval_type, source) if approval_type else None
    marketing_policy_decision = _marketing_policy_decision_for_approval(action, source)

    approval_id = _string_or_none(source.get("approval_id") or source.get("id"))
    created_at = _parse_datetime(source.get("created_at")) or now
    due_at = _parse_datetime(source.get("due_at") or source.get("expires_at"))
    if due_at is None and policy is not None:
        due_at = created_at + timedelta(seconds=int(policy["default_sla_seconds"]))
    if due_at is None:
        due_at = created_at

    status = _normalize_key(source.get("status") or "pending")
    if status and status != "pending":
        return {
            "approval_id": approval_id,
            "approval_type": approval_type,
            "status": status,
            "timed_out": False,
            "created_at": created_at.isoformat(),
            "due_at": due_at.isoformat(),
            "timed_out_at": None,
            "outcome": "already_resolved",
            "workflow_state": "resolved",
            "external_writes_allowed": False,
            "progress_allowed": False,
            "read_only_continuation_allowed": False,
            "next_action_cta": "none",
            "audit_evidence": None,
        }

    if policy is None:
        return _timeout_decision(
            approval=source,
            policy=_missing_policy(approval_type),
            created_at=created_at,
            due_at=due_at,
            timed_out_at=now if now > due_at else None,
            action=action,
            outcome="require_manual_resolution",
            policy_missing=True,
            marketing_policy_decision=marketing_policy_decision,
        )

    if now <= due_at:
        return {
            "approval_id": approval_id,
            "approval_type": approval_type,
            "status": "pending",
            "timed_out": False,
            "created_at": created_at.isoformat(),
            "due_at": due_at.isoformat(),
            "timed_out_at": None,
            "outcome": None,
            "workflow_state": "waiting_hitl",
            "external_writes_allowed": False,
            "progress_allowed": False,
            "read_only_continuation_allowed": False,
            "next_action_cta": "wait_for_approval",
            "audit_evidence": None,
        }

    return _timeout_decision(
        approval=source,
        policy=policy,
        created_at=created_at,
        due_at=due_at,
        timed_out_at=now,
        action=action,
        outcome=str(policy["timeout_outcome"]),
        policy_missing=False,
        marketing_policy_decision=marketing_policy_decision,
    )


def build_approval_timeout_risk(
    approvals: Iterable[dict[str, Any]],
    *,
    policy_source: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a dashboard/API projection for pending and overdue approvals."""

    decisions = [
        evaluate_approval_timeout(approval, policy_source=policy_source, now=now)
        for approval in approvals
    ]
    counts = dict.fromkeys(APPROVAL_TIMEOUT_OUTCOMES, 0)
    for decision in decisions:
        outcome = str(decision.get("outcome") or "")
        if outcome in counts:
            counts[outcome] += 1

    pending = sum(1 for decision in decisions if not decision.get("timed_out") and decision.get("status") == "pending")
    timed_out = sum(1 for decision in decisions if decision.get("timed_out"))
    blocked_external_writes = sum(
        1
        for decision in decisions
        if decision.get("timed_out") and not decision.get("external_writes_allowed")
    )
    return {
        "status": "blocked" if timed_out else "pending" if pending else "ready",
        "total": len(decisions),
        "pending": pending,
        "overdue": timed_out,
        "timed_out": timed_out,
        "blocked_external_writes": blocked_external_writes,
        **counts,
        "next_action_cta": (
            "resolve_overdue_approvals"
            if timed_out
            else "review_pending_approvals"
            if pending
            else "none"
        ),
        "approval_timeout_decisions": decisions,
    }


def approval_timeout_allows_external_write(decision: dict[str, Any] | None) -> bool:
    """Return false when a timed-out approval blocks external writes."""

    if not isinstance(decision, dict):
        return True
    if not decision.get("timed_out"):
        return True
    return bool(decision.get("external_writes_allowed"))


def _timeout_decision(
    *,
    approval: dict[str, Any],
    policy: dict[str, Any],
    created_at: datetime,
    due_at: datetime,
    timed_out_at: datetime | None,
    action: Any,
    outcome: str,
    policy_missing: bool,
    marketing_policy_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    timed_out = timed_out_at is not None
    normalized_outcome = outcome if outcome in APPROVAL_TIMEOUT_OUTCOMES else "require_manual_resolution"
    external_allowed = bool(
        timed_out
        and policy.get("external_writes_allowed_after_timeout")
        and (
            policy.get("preapproved_after_timeout")
            or approval.get("preapproved_after_timeout")
            or approval.get("external_writes_preapproved_after_timeout")
        )
    )
    workflow_state = {
        "auto_cancel": "cancelled",
        "auto_escalate": "waiting_hitl",
        "continue_read_only": "degraded",
        "pause_workflow": "paused",
        "require_manual_resolution": "blocked",
    }[normalized_outcome]
    next_action = _next_action_for_outcome(normalized_outcome, policy_missing, policy)
    policy_required_role = _policy_required_role(marketing_policy_decision)
    escalation_decision = _approval_escalation_decision(
        approval,
        policy,
        action,
        normalized_outcome,
        timed_out_at or due_at,
    )
    audit_evidence = (
        _audit_evidence(
            approval,
            policy,
            created_at,
            due_at,
            timed_out_at,
            action,
            normalized_outcome,
            external_allowed,
            policy_missing,
            policy_required_role,
            escalation_decision,
        )
        if timed_out
        else None
    )
    result = {
        "approval_id": _string_or_none(approval.get("approval_id") or approval.get("id")),
        "approval_type": policy.get("approval_type"),
        "status": "timed_out" if timed_out else "pending",
        "timed_out": timed_out,
        "policy_missing": policy_missing,
        "created_at": created_at.isoformat(),
        "due_at": due_at.isoformat(),
        "timed_out_at": timed_out_at.isoformat() if timed_out_at else None,
        "outcome": normalized_outcome,
        "workflow_state": workflow_state,
        "step_status": _step_status_for_outcome(normalized_outcome),
        "escalated": normalized_outcome == "auto_escalate",
        "escalation_target": policy.get("escalation_role"),
        "policy_required_role": policy_required_role,
        "external_writes_allowed": external_allowed,
        "progress_allowed": normalized_outcome == "continue_read_only",
        "read_only_continuation_allowed": normalized_outcome == "continue_read_only",
        "blocked_action": _normalize_key(action),
        "next_action_cta": next_action,
        "safe_fallback_message": policy.get("safe_fallback_message"),
        "escalation_decision": escalation_decision,
        "escalation_evidence": escalation_decision.get("evidence"),
        "audit_evidence": audit_evidence,
        "audit_reference": (audit_evidence or {}).get("audit_reference"),
        "marketing_policy_decision": marketing_policy_decision,
    }
    if audit_evidence is not None:
        audit_package = build_approval_timeout_decision_audit(result, now=timed_out_at or due_at)
        result["decision_audit"] = audit_package
        result["decision_audit_ref"] = audit_package["audit_reference"]
        audit_evidence["decision_audit"] = audit_package
        audit_evidence["decision_audit_ref"] = audit_package["audit_reference"]
    return result


def _audit_evidence(
    approval: dict[str, Any],
    policy: dict[str, Any],
    created_at: datetime,
    due_at: datetime,
    timed_out_at: datetime,
    action: Any,
    outcome: str,
    external_allowed: bool,
    policy_missing: bool,
    policy_required_role: str | None,
    escalation_decision: dict[str, Any],
) -> dict[str, Any]:
    approval_id = _string_or_none(approval.get("approval_id") or approval.get("id"))
    workflow_id = _string_or_none(approval.get("workflow_id"))
    workflow_run_id = _string_or_none(
        approval.get("workflow_run_id") or approval.get("run_id") or approval.get("engine_run_id")
    )
    step_id = _string_or_none(approval.get("step_id"))
    blocked_action = _normalize_key(action)
    audit_reference = _audit_reference(approval_id, workflow_id, workflow_run_id, step_id, blocked_action, outcome)
    return {
        "event_type": policy.get("audit_event_code"),
        "notification_event_code": policy.get("notification_event_code"),
        "approval_id": approval_id,
        "workflow_id": workflow_id,
        "workflow_run_id": workflow_run_id,
        "run_id": _string_or_none(approval.get("run_id")),
        "step_id": step_id,
        "requested_approver": _string_or_none(
            approval.get("requested_approver") or approval.get("approver") or approval.get("assignee")
        ),
        "requested_approver_role": _string_or_none(
            approval.get("requested_approver_role")
            or approval.get("assignee_role")
            or approval.get("requested_role")
        ),
        "created_at": created_at.isoformat(),
        "due_at": due_at.isoformat(),
        "timed_out_at": timed_out_at.isoformat(),
        "outcome": outcome,
        "escalation_target": policy.get("escalation_role"),
        "policy_required_role": policy_required_role,
        "blocked_action": blocked_action,
        "external_writes_allowed": external_allowed,
        "policy_missing": policy_missing,
        "escalation_decision": escalation_decision,
        "escalation_evidence": escalation_decision.get("evidence"),
        "notification_channels": escalation_decision.get("notification_channels") or [],
        "audit_reference": audit_reference,
    }


def _approval_escalation_decision(
    approval: dict[str, Any],
    policy: dict[str, Any],
    action: Any,
    outcome: str,
    now: datetime,
) -> dict[str, Any]:
    return evaluate_marketing_escalation(
        {
            **approval,
            "trigger_type": "approval_timeout",
            "severity": "high",
            "action": action,
            "blocked_action": action,
            "workflow_id": approval.get("workflow_id"),
            "workflow_run_id": approval.get("workflow_run_id") or approval.get("run_id"),
            "step_id": approval.get("step_id"),
            "fallback_outcome": outcome,
            "reason": policy.get("safe_fallback_message"),
        },
        now=now,
    )


def _marketing_policy_decision_for_approval(
    action: Any,
    source: dict[str, Any],
) -> dict[str, Any] | None:
    existing = source.get("marketing_policy_decision") or source.get("cmo_marketing_policy_decision")
    if isinstance(existing, dict):
        return existing
    if not action:
        return None
    context = {
        **source,
        "action": action,
        "workflow_mode": source.get("workflow_mode") or source.get("mode") or "active",
        "workflow_id": source.get("workflow_id"),
        "external_write_required": source.get("external_write_required", True),
        "customer_facing": source.get("customer_facing", True),
    }
    return evaluate_marketing_policy(context)


def _policy_required_role(marketing_policy_decision: dict[str, Any] | None) -> str | None:
    if not isinstance(marketing_policy_decision, dict):
        return None
    return _string_or_none(
        marketing_policy_decision.get("required_escalation_role")
        or marketing_policy_decision.get("required_approver_role")
    )


def _policy_override(approval_type: str, source: dict[str, Any]) -> dict[str, Any] | None:
    candidates = (
        source.get("approval_timeout_policies"),
        source.get("approval_timeouts"),
        source.get("timeout_policies"),
    )
    for candidate in candidates:
        if isinstance(candidate, dict):
            value = candidate.get(approval_type)
            if isinstance(value, dict):
                return value

    single = source.get("approval_timeout_policy") or source.get("timeout_policy")
    if isinstance(single, dict):
        single_type = _normalize_key(single.get("approval_type") or single.get("type"))
        if not single_type or single_type == approval_type:
            return single
    return None


def _merge_policy_override(policy: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(policy)
    if override.get("default_sla_seconds") is not None:
        merged["default_sla_seconds"] = _int_or_default(
            override.get("default_sla_seconds"),
            merged["default_sla_seconds"],
        )
    elif override.get("sla_minutes") is not None or override.get("default_sla_minutes") is not None:
        minutes = _int_or_default(override.get("sla_minutes") or override.get("default_sla_minutes"), 0)
        merged["default_sla_seconds"] = max(minutes * 60, 0)
    elif override.get("sla_hours") is not None or override.get("timeout_hours") is not None:
        hours = _float_or_default(override.get("sla_hours") or override.get("timeout_hours"), 0.0)
        merged["default_sla_seconds"] = int(max(hours, 0.0) * 3600)

    merged["default_sla_hours"] = merged["default_sla_seconds"] / 3600
    if override.get("escalation_owner_role") or override.get("escalation_role"):
        merged["escalation_role"] = str(override.get("escalation_owner_role") or override.get("escalation_role"))
    if override.get("timeout_outcome") or override.get("outcome"):
        outcome = _normalize_key(override.get("timeout_outcome") or override.get("outcome"))
        merged["timeout_outcome"] = outcome if outcome in APPROVAL_TIMEOUT_OUTCOMES else "require_manual_resolution"
    for field in (
        "external_writes_allowed_after_timeout",
        "preapproved_after_timeout",
        "notification_event_code",
        "audit_event_code",
        "safe_fallback_cta",
        "safe_fallback_message",
    ):
        if field in override:
            merged[field] = override[field]
    return merged


def _minimal_policy(approval_type: str) -> dict[str, Any]:
    return {
        "approval_type": approval_type,
        "actions": [],
        "default_sla_seconds": 0,
        "default_sla_hours": 0,
        "escalation_role": "cmo",
        "timeout_outcome": "require_manual_resolution",
        "external_writes_allowed_after_timeout": False,
        "preapproved_after_timeout": False,
        "notification_event_code": "cmo_approval_timeout_unknown",
        "audit_event_code": "cmo_approval_timeout_manual_resolution",
        "safe_fallback_cta": "configure_approval_timeout_policy",
        "safe_fallback_message": "Approval timeout policy is missing; manual resolution is required.",
    }


def _missing_policy(approval_type: str | None) -> dict[str, Any]:
    policy = _minimal_policy(approval_type or "unknown")
    policy["notification_event_code"] = "cmo_approval_timeout_policy_missing"
    policy["audit_event_code"] = "cmo_approval_timeout_policy_missing"
    return policy


def _next_action_for_outcome(
    outcome: str,
    policy_missing: bool,
    policy: dict[str, Any],
) -> str:
    if policy_missing:
        return "configure_approval_timeout_policy"
    if outcome == "auto_cancel":
        return "restart_approval"
    if outcome == "auto_escalate":
        return "review_escalated_approval"
    if outcome == "continue_read_only":
        return "continue_read_only"
    if outcome == "pause_workflow":
        return "resume_after_manual_approval"
    return str(policy.get("safe_fallback_cta") or "manual_resolution_required")


def _step_status_for_outcome(outcome: str) -> str:
    if outcome == "auto_cancel":
        return "cancelled"
    if outcome == "continue_read_only":
        return "completed"
    return "waiting_hitl"


def _dedupe_policies(policies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for policy in policies:
        key = str(policy.get("approval_type") or "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(policy)
    return unique


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _list_from_value(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
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


def _ensure_aware(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _audit_reference(*parts: Any) -> str:
    source = "|".join(str(part or "") for part in parts)
    return f"mkt_approval_timeout_{hashlib.sha256(source.encode()).hexdigest()[:20]}"


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
