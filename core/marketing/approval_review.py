"""CMO approval review projection and decision request helpers.

CMO-8.3 turns pending marketing HITL rows into an actionable approval-review
payload. The projection is intentionally storage-free: it gives reviewers the
preview, diff, impact, risk, policy, escalation, timeout, write-readiness, and
audit evidence needed to decide safely, while preserving the existing HITL
decision endpoint as the source of persistence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from core.marketing.approval_timeouts import (
    approval_type_for_action,
    evaluate_approval_timeout,
)
from core.marketing.decision_audit import build_cmo_decision_audit_package
from core.marketing.escalation_matrix import evaluate_marketing_escalation
from core.marketing.policy_manifest import (
    CUSTOMER_FACING_WRITE_ACTIONS,
    evaluate_marketing_policy,
)

CMO_APPROVAL_REVIEW_VERSION = "2026-05-23.cmo-8.3"

APPROVAL_REVIEW_STATUSES = (
    "pending",
    "approved",
    "rejected",
    "overridden",
    "timed_out",
    "escalated",
    "blocked",
)

APPROVAL_REVIEW_ACTION_TYPES = (
    "campaign_launch",
    "ad_budget_change",
    "content_publish",
    "email_send",
    "landing_page_change",
    "target_account_list_change",
    "crisis_public_response",
    "high_risk_copy_pricing_legal_claim",
    "workflow_promotion",
)

REVIEWER_ACTIONS = (
    "approve",
    "reject",
    "override",
    "escalate",
    "request_changes",
    "pause",
)

ACTION_TYPE_ALIASES = {
    "ad_campaign_launch": "campaign_launch",
    "campaign_launch": "campaign_launch",
    "launch_campaign": "campaign_launch",
    "create_campaign": "campaign_launch",
    "activate_campaign": "campaign_launch",
    "ad_budget_change": "ad_budget_change",
    "budget_change": "ad_budget_change",
    "mutate_ad_budget": "ad_budget_change",
    "update_ad_budget": "ad_budget_change",
    "spend": "ad_budget_change",
    "content_publish": "content_publish",
    "publish": "content_publish",
    "publish_to_wordpress": "content_publish",
    "schedule_content": "content_publish",
    "email_send": "email_send",
    "send": "email_send",
    "send_email": "email_send",
    "send_to_segment": "email_send",
    "landing_page_change": "landing_page_change",
    "publish_landing_page": "landing_page_change",
    "update_landing_page": "landing_page_change",
    "target_account_list_change": "target_account_list_change",
    "target_account_change": "target_account_list_change",
    "update_target_accounts": "target_account_list_change",
    "crisis_public_response": "crisis_public_response",
    "crisis_response": "crisis_public_response",
    "public_response": "crisis_public_response",
    "high_risk_copy_or_pricing_claims": "high_risk_copy_pricing_legal_claim",
    "high_risk_copy_pricing_legal_claim": "high_risk_copy_pricing_legal_claim",
    "high_risk_copy": "high_risk_copy_pricing_legal_claim",
    "pricing_claim": "high_risk_copy_pricing_legal_claim",
    "legal_claim": "high_risk_copy_pricing_legal_claim",
    "workflow_promotion": "workflow_promotion",
    "promote_workflow": "workflow_promotion",
}

CUSTOMER_FACING_ACTION_TYPES = {
    "campaign_launch",
    "ad_budget_change",
    "content_publish",
    "email_send",
    "landing_page_change",
    "target_account_list_change",
    "crisis_public_response",
    "high_risk_copy_pricing_legal_claim",
}


def build_cmo_approval_review_projection(
    approval_records: Iterable[Mapping[str, Any]] = (),
    *,
    connector_contracts: Iterable[Mapping[str, Any]] = (),
    policy_projection: Mapping[str, Any] | None = None,
    escalation_projection: Mapping[str, Any] | None = None,
    decision_audit_projection: Mapping[str, Any] | None = None,
    approval_timeout_risk: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build CMO approval-review cards from HITL/approval records."""

    timestamp = _ensure_aware(now) or datetime.now(UTC)
    timeout_by_id = _timeout_decisions_by_id(approval_timeout_risk)
    contracts = _dicts(connector_contracts)
    reviews = [
        _build_approval_review(
            record,
            connector_contracts=contracts,
            policy_projection=policy_projection,
            escalation_projection=escalation_projection,
            decision_audit_projection=decision_audit_projection,
            timeout_decision=timeout_by_id.get(_approval_id(record)),
            now=timestamp,
        )
        for record in _dicts(approval_records)
    ]
    reviews = sorted(
        reviews,
        key=lambda row: (
            _status_sort(row["status"]),
            _parse_datetime(row.get("due_at")) or datetime.max.replace(tzinfo=UTC),
            row["approval_id"],
        ),
    )
    return {
        "cmo_approval_review_version": CMO_APPROVAL_REVIEW_VERSION,
        "cmo_approval_reviews": reviews,
        "cmo_approval_review_summary": summarize_cmo_approval_reviews(reviews),
    }


def summarize_cmo_approval_reviews(
    reviews: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = _dicts(reviews)
    counts = dict.fromkeys(APPROVAL_REVIEW_STATUSES, 0)
    unsafe_write = 0
    missing_audit = 0
    ready = 0
    for row in rows:
        status = _normalize_key(row.get("status"))
        if status in counts:
            counts[status] += 1
        write = row.get("external_write_readiness")
        if isinstance(write, Mapping) and write.get("status") == "unsafe":
            unsafe_write += 1
        audit = row.get("audit_evidence")
        if isinstance(audit, Mapping) and not audit.get("ready"):
            missing_audit += 1
        if "approve" in _string_list(row.get("allowed_reviewer_actions")):
            ready += 1

    first = rows[0] if rows else None
    readiness = (
        "blocked"
        if counts["blocked"] or counts["timed_out"] or unsafe_write or missing_audit
        else "pending"
        if counts["pending"] or counts["escalated"]
        else "ready"
    )
    return {
        "schema_version": CMO_APPROVAL_REVIEW_VERSION,
        "total": len(rows),
        "readiness": readiness,
        "approval_ready": ready,
        "unsafe_write": unsafe_write,
        "missing_audit": missing_audit,
        "needs_action": sum(1 for row in rows if _review_needs_action(row)),
        "first_approval_review_id": first.get("approval_review_id") if first else None,
        "next_action_cta": first.get("next_action_cta") if first else _cta("none"),
        **counts,
    }


def build_cmo_approval_decision_request(
    review: Mapping[str, Any],
    *,
    decision: str,
    actor_id: str,
    actor_role: str,
    reason: str | None = None,
    replacement_action: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a deterministic request shape for the existing HITL endpoint.

    This helper does not persist a decision. It validates that the requested
    reviewer action is allowed by the current review projection and returns the
    shape the approval endpoint or a future approval UI can submit.
    """

    row = dict(review)
    normalized_decision = _normalize_key(decision)
    allowed = set(_string_list(row.get("allowed_reviewer_actions")))
    if normalized_decision not in REVIEWER_ACTIONS:
        raise ValueError(f"Unsupported CMO approval decision: {decision}")
    if normalized_decision not in allowed:
        raise ValueError(
            f"Decision {normalized_decision} is not allowed for approval "
            f"{row.get('approval_id') or 'unknown'}"
        )
    if normalized_decision in {"reject", "override", "request_changes", "escalate", "pause"} and not reason:
        raise ValueError(f"Decision {normalized_decision} requires a reason")
    if normalized_decision == "override" and not isinstance(replacement_action, Mapping):
        raise ValueError("Override requires a replacement_action payload")

    timestamp = (_ensure_aware(now) or datetime.now(UTC)).isoformat()
    request = {
        "approval_id": row.get("approval_id"),
        "approval_review_id": row.get("approval_review_id"),
        "decision": normalized_decision,
        "actor_type": "user",
        "actor_id": actor_id,
        "actor_role": actor_role,
        "reason": reason,
        "replacement_action": dict(replacement_action) if isinstance(replacement_action, Mapping) else None,
        "decided_at": timestamp,
        "policy_result_ref": row.get("policy_result_ref"),
        "escalation_result_ref": row.get("escalation_result_ref"),
        "timeout_result_ref": row.get("timeout_result_ref"),
        "external_write_readiness_ref": row.get("external_write_readiness", {}).get("readiness_ref")
        if isinstance(row.get("external_write_readiness"), Mapping)
        else None,
        "audit_refs": _unique_strings([*(_string_list(row.get("audit_refs"))), row.get("review_audit_ref")]),
    }
    audit = build_cmo_decision_audit_package(
        {
            "event_type": "override" if normalized_decision == "override" else row.get("action_type"),
            "decision_type": normalized_decision,
            "approval_result": request,
            "workflow_id": row.get("workflow_id"),
            "workflow_run_id": row.get("workflow_run_id"),
            "step_id": row.get("step_id"),
            "action": row.get("action"),
            "actor_type": "user",
            "actor_id": actor_id,
            "actor_role": actor_role,
            "rationale": reason,
            "override": {
                "actor_id": actor_id,
                "actor_role": actor_role,
                "reason": reason,
                "replacement_action": normalized_decision == "override",
                "replacement_action_ref": (replacement_action or {}).get("action_id")
                if isinstance(replacement_action, Mapping)
                else None,
            }
            if normalized_decision == "override"
            else None,
            "final_outcome": normalized_decision,
        },
        now=_ensure_aware(now),
    )
    request["decision_audit"] = audit
    request["decision_audit_ref"] = audit["audit_reference"]
    return request


def _build_approval_review(
    approval: Mapping[str, Any],
    *,
    connector_contracts: list[dict[str, Any]],
    policy_projection: Mapping[str, Any] | None,
    escalation_projection: Mapping[str, Any] | None,
    decision_audit_projection: Mapping[str, Any] | None,
    timeout_decision: Mapping[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    record = dict(approval)
    approval_id = _approval_id(record) or _stable_hash("approval", record)
    action = _normalize_key(
        record.get("action")
        or record.get("approval_action")
        or record.get("blocked_action")
        or record.get("trigger_type")
    )
    action_type = _approval_action_type(action, record)
    workflow_mode = _normalize_key(record.get("workflow_mode") or record.get("mode") or "active")
    customer_facing = _customer_facing(record, action, action_type)
    external_required = _external_write_required(record, action, action_type)
    policy_result = _policy_result(record, action, workflow_mode, customer_facing, external_required, policy_projection)
    escalation_result = _escalation_result(record, action, policy_result, escalation_projection, now)
    timeout_result = dict(timeout_decision) if isinstance(timeout_decision, Mapping) else _timeout_result(record, now)
    write_readiness = _external_write_readiness(
        record,
        action,
        action_type,
        connector_contracts,
        external_required,
    )
    audit_evidence = _audit_evidence(record, policy_result, decision_audit_projection, customer_facing)
    blockers = _blockers(
        policy_result=policy_result,
        escalation_result=escalation_result,
        timeout_result=timeout_result,
        write_readiness=write_readiness,
        audit_evidence=audit_evidence,
        customer_facing=customer_facing,
    )
    status = _review_status(record, timeout_result, escalation_result, blockers)
    allowed_actions = _allowed_actions(status, blockers, policy_result, escalation_result, audit_evidence)
    review_id = _approval_review_id(approval_id, action, record.get("workflow_run_id"), record.get("step_id"))
    review_audit = build_cmo_decision_audit_package(
        {
            "event_type": action_type,
            "decision_type": status,
            "workflow_id": record.get("workflow_id"),
            "workflow_run_id": record.get("workflow_run_id") or record.get("run_id"),
            "run_id": record.get("run_id"),
            "step_id": record.get("step_id"),
            "agent": record.get("agent_type") or record.get("agent_id"),
            "action": action,
            "actor_type": "system",
            "source_refs": _source_refs(record),
            "connector_refs": _connector_refs(record, connector_contracts),
            "policy_result": policy_result,
            "escalation_result": escalation_result,
            "timeout_result": timeout_result,
            "external_write_result": write_readiness,
            "rationale": _agent_rationale(record),
            "risk_flags": _risk_flags(record, policy_result, escalation_result, timeout_result, write_readiness),
            "confidence": _float_or_none(record.get("confidence")),
            "final_outcome": status,
        },
        now=now,
    )
    next_action = _next_action(status, blockers, policy_result, escalation_result, timeout_result, write_readiness)
    return {
        "approval_review_id": review_id,
        "approval_id": approval_id,
        "workflow_id": _string_or_none(record.get("workflow_id")),
        "workflow_run_id": _string_or_none(record.get("workflow_run_id") or record.get("run_id")),
        "run_id": _string_or_none(record.get("run_id")),
        "step_id": _string_or_none(record.get("step_id")),
        "action": action,
        "action_type": action_type,
        "status": status,
        "requester": _string_or_none(record.get("requester") or record.get("requested_by")),
        "agent_ref": _string_or_none(record.get("agent_ref") or record.get("agent_type") or record.get("agent_id")),
        "actor_refs": {
            "requester": _string_or_none(record.get("requester") or record.get("requested_by")),
            "agent_id": _string_or_none(record.get("agent_id")),
            "actor_id": _string_or_none(record.get("actor_id")),
        },
        "assigned_approver": _string_or_none(record.get("assigned_approver") or record.get("requested_approver")),
        "assigned_approver_role": _string_or_none(
            record.get("assigned_approver_role")
            or record.get("requested_approver_role")
            or record.get("assignee_role")
        ),
        "created_at": _datetime_string(record.get("created_at"), now),
        "due_at": _datetime_string(record.get("due_at") or record.get("expires_at"), None),
        "timeout_state": _timeout_state(timeout_result),
        "preview_payload": _preview_payload(record),
        "before_after_diff": _before_after_diff(record),
        "budget_impact": _budget_impact(record),
        "audience_impact": _audience_impact(record),
        "risk_flags": _risk_flags(record, policy_result, escalation_result, timeout_result, write_readiness),
        "source_refs": _source_refs(record),
        "connector_refs": _connector_refs(record, connector_contracts),
        "agent_rationale": _agent_rationale(record),
        "policy_result": policy_result,
        "policy_result_ref": _result_ref(policy_result, "policy"),
        "escalation_result": escalation_result,
        "escalation_result_ref": _result_ref(escalation_result, "escalation"),
        "timeout_result": timeout_result,
        "timeout_result_ref": _result_ref(timeout_result, "timeout"),
        "external_write_readiness": write_readiness,
        "external_write_result_ref": write_readiness.get("readiness_ref"),
        "audit_evidence": audit_evidence,
        "audit_refs": audit_evidence["audit_refs"],
        "review_audit_ref": review_audit["audit_reference"],
        "review_audit": review_audit,
        "rollback_stop_plan": _rollback_stop_plan(record, action_type),
        "allowed_reviewer_actions": allowed_actions,
        "blocked_reasons": blockers,
        "related_work_queue_item_ids": [_work_queue_item_id(approval_id)],
        "next_action_cta": _cta(next_action),
        "evaluated_at": now.isoformat(),
    }


def _policy_result(
    record: Mapping[str, Any],
    action: str,
    workflow_mode: str,
    customer_facing: bool,
    external_required: bool,
    policy_projection: Mapping[str, Any] | None,
) -> dict[str, Any]:
    existing = record.get("marketing_policy_decision") or record.get("policy_result") or record.get("policy_decision")
    if isinstance(existing, Mapping):
        return dict(existing)
    manifest = None
    if isinstance(policy_projection, Mapping):
        candidate = policy_projection.get("marketing_policy_manifest")
        if isinstance(candidate, Mapping):
            manifest = candidate
    return evaluate_marketing_policy(
        {
            **dict(record),
            "action": action,
            "workflow_mode": workflow_mode,
            "workflow_id": record.get("workflow_id"),
            "external_write_required": external_required,
            "customer_facing": customer_facing,
        },
        manifest=manifest,
        use_default=manifest is None,
    )


def _escalation_result(
    record: Mapping[str, Any],
    action: str,
    policy_result: Mapping[str, Any],
    escalation_projection: Mapping[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    existing = record.get("escalation_decision") or record.get("escalation_result")
    if isinstance(existing, Mapping):
        return dict(existing)
    nested = policy_result.get("escalation_decision")
    if isinstance(nested, Mapping) and nested.get("decision") != "no_escalation":
        return dict(nested)
    matrix = None
    if isinstance(escalation_projection, Mapping):
        candidate = escalation_projection.get("marketing_escalation_matrix")
        if isinstance(candidate, Mapping):
            matrix = candidate
    return evaluate_marketing_escalation(
        {
            **dict(record),
            "action": action,
            "workflow_id": record.get("workflow_id"),
            "workflow_run_id": record.get("workflow_run_id") or record.get("run_id"),
            "step_id": record.get("step_id"),
            "trigger_type": record.get("escalation_trigger_type") or record.get("trigger_type"),
            "external_write_required": _truthy(record.get("external_write_required")),
            "reason": policy_result.get("reason") or record.get("reason"),
        },
        matrix=matrix,
        use_default=matrix is None,
        now=now,
    )


def _timeout_result(record: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    existing = record.get("approval_timeout_decision") or record.get("timeout_result")
    if isinstance(existing, Mapping):
        return dict(existing)
    return evaluate_approval_timeout(dict(record), policy_source=dict(record), now=now)


def _external_write_readiness(
    record: Mapping[str, Any],
    action: str,
    action_type: str,
    connector_contracts: list[dict[str, Any]],
    external_required: bool,
) -> dict[str, Any]:
    if not external_required:
        return {
            "status": "not_required",
            "write_safe": True,
            "reason": "Approval is not for an external/customer-facing write.",
            "connector_keys": [],
            "readiness_ref": _stable_ref("approval_write_readiness", action, "not_required"),
            "next_action_cta": "none",
        }

    connector_keys = _connector_keys(record)
    if not connector_keys:
        return {
            "status": "unsafe",
            "write_safe": False,
            "reason": "External/customer-facing approval has no connector key or connector ref.",
            "connector_keys": [],
            "readiness_ref": _stable_ref("approval_write_readiness", action, "missing_connector"),
            "next_action_cta": "configure_connector_contract",
        }

    unsafe: list[str] = []
    checked: list[dict[str, Any]] = []
    for key in connector_keys:
        row = _contract_for_key(connector_contracts, key)
        if row is None:
            unsafe.append(f"{key}: connector contract missing")
            checked.append({"connector_key": key, "write_status": "missing"})
            continue
        write_safe = bool(row.get("write_safe", row.get("write_ready", False)))
        write_status = _normalize_key(row.get("write_status") or row.get("contract_state"))
        missing_scopes = _string_list(row.get("missing_write_scopes"))
        blocks_write = bool(row.get("blocks_external_writes"))
        mock_only = bool(row.get("mock_or_test_double"))
        if (
            not write_safe
            or write_status not in {"ready", "write_confirmed", ""}
            or missing_scopes
            or blocks_write
            or mock_only
        ):
            unsafe.append(
                f"{key}: write status {write_status or 'unknown'}"
                + (f", missing scopes {', '.join(missing_scopes)}" if missing_scopes else "")
            )
        checked.append(
            {
                "connector_key": key,
                "write_status": write_status or "unknown",
                "write_safe": write_safe,
                "missing_write_scopes": missing_scopes,
                "mock_or_test_double": mock_only,
            }
        )

    if unsafe:
        return {
            "status": "unsafe",
            "write_safe": False,
            "reason": "; ".join(unsafe),
            "connector_keys": connector_keys,
            "checked_contracts": checked,
            "readiness_ref": _stable_ref("approval_write_readiness", action_type, *unsafe),
            "next_action_cta": "fix_connector_write_readiness",
        }
    return {
        "status": "safe",
        "write_safe": True,
        "reason": (
            "Required connector contracts are write-safe; final workflow "
            "completion still requires CMO-5.3 write confirmation."
        ),
        "connector_keys": connector_keys,
        "checked_contracts": checked,
        "readiness_ref": _stable_ref("approval_write_readiness", action_type, *connector_keys),
        "next_action_cta": "none",
    }


def _audit_evidence(
    record: Mapping[str, Any],
    policy_result: Mapping[str, Any],
    decision_audit_projection: Mapping[str, Any] | None,
    customer_facing: bool,
) -> dict[str, Any]:
    audit_refs = _audit_refs(record)
    disabled = _truthy(record.get("decision_audit_disabled") or record.get("audit_package_disabled"))
    projection_summary = (
        decision_audit_projection.get("marketing_decision_audit_summary")
        if isinstance(decision_audit_projection, Mapping)
        else None
    )
    projection_ready = not (
        isinstance(projection_summary, Mapping)
        and _normalize_key(projection_summary.get("status")) == "missing_audit_evidence"
    )
    required = _string_list(policy_result.get("required_audit_evidence")) or (
        ["approval_record", "policy_decision", "workflow_run", "actor"] if customer_facing else []
    )
    ready = bool(audit_refs) and projection_ready and not disabled
    missing = [] if ready else required or ["audit_reference"]
    return {
        "ready": ready,
        "required_evidence": required,
        "missing_evidence": missing,
        "audit_refs": audit_refs,
        "projection_ready": projection_ready,
        "reason": None if ready else "Required CMO decision-audit evidence is missing.",
    }


def _blockers(
    *,
    policy_result: Mapping[str, Any],
    escalation_result: Mapping[str, Any],
    timeout_result: Mapping[str, Any],
    write_readiness: Mapping[str, Any],
    audit_evidence: Mapping[str, Any],
    customer_facing: bool,
) -> list[str]:
    blockers: list[str] = []
    policy_decision = _normalize_key(policy_result.get("decision"))
    if policy_decision == "missing_policy":
        blockers.append("Marketing policy result is missing for this approval.")
    elif policy_decision in {"blocked", "read_only_only"}:
        blockers.append(str(policy_result.get("reason") or "Marketing policy blocks this approval."))

    if policy_decision == "requires_escalation" and not _truthy(policy_result.get("escalation_satisfied")):
        if not escalation_result.get("route_found"):
            blockers.append("Required escalation route is missing for this approval.")
        else:
            blockers.append("Policy requires escalation before approval can proceed.")

    if timeout_result.get("timed_out"):
        outcome = _normalize_key(timeout_result.get("outcome"))
        blockers.append(
            "Approval timed out and requires manual resolution."
            if outcome == "require_manual_resolution"
            else "Approval timed out; reviewer must resolve timeout outcome before approving."
        )

    if customer_facing and write_readiness.get("write_safe") is False:
        blockers.append(str(write_readiness.get("reason") or "Connector/write readiness is unsafe."))

    if customer_facing and not audit_evidence.get("ready"):
        blockers.append(str(audit_evidence.get("reason") or "Required audit evidence is missing."))

    return _unique_strings(blockers)


def _review_status(
    record: Mapping[str, Any],
    timeout_result: Mapping[str, Any],
    escalation_result: Mapping[str, Any],
    blockers: list[str],
) -> str:
    source_status = _normalize_key(record.get("status") or "pending")
    decision = _normalize_key(record.get("decision"))
    if source_status in {"approved", "rejected", "overridden", "timed_out", "escalated", "blocked"}:
        return source_status
    if source_status in {"decided", "completed"}:
        if decision in {"reject", "rejected"}:
            return "rejected"
        if decision in {"override", "overridden"}:
            return "overridden"
        return "approved"
    if timeout_result.get("timed_out"):
        return "timed_out"
    if blockers:
        return "blocked"
    return "pending"


def _allowed_actions(
    status: str,
    blockers: list[str],
    policy_result: Mapping[str, Any],
    escalation_result: Mapping[str, Any],
    audit_evidence: Mapping[str, Any],
) -> list[str]:
    if status in {"approved", "rejected", "overridden"}:
        return []
    if status == "timed_out":
        actions = ["reject", "request_changes", "pause"]
        if escalation_result.get("route_found"):
            actions.insert(0, "escalate")
        return actions
    if blockers:
        actions = ["reject", "request_changes", "pause"]
        if escalation_result.get("route_found"):
            actions.insert(0, "escalate")
        if audit_evidence.get("ready") and _normalize_key(policy_result.get("decision")) not in {
            "missing_policy",
            "blocked",
            "read_only_only",
            "requires_escalation",
        }:
            actions.insert(0, "override")
        return _unique_strings(actions)

    actions = ["approve", "reject", "override", "request_changes", "pause"]
    if _normalize_key(policy_result.get("decision")) == "requires_escalation":
        actions = [action for action in actions if action != "approve"]
    if escalation_result.get("route_found") and _normalize_key(escalation_result.get("decision")) != "no_escalation":
        actions.insert(0, "escalate")
    return _unique_strings(actions)


def _next_action(
    status: str,
    blockers: list[str],
    policy_result: Mapping[str, Any],
    escalation_result: Mapping[str, Any],
    timeout_result: Mapping[str, Any],
    write_readiness: Mapping[str, Any],
) -> str:
    text = " ".join(blockers).lower()
    if status == "timed_out" or timeout_result.get("timed_out"):
        return str(timeout_result.get("next_action_cta") or "resolve_overdue_approvals")
    if "policy" in text:
        return str(policy_result.get("next_action_cta") or "configure_marketing_policy_manifest")
    if "connector" in text or "write" in text:
        return str(write_readiness.get("next_action_cta") or "fix_connector_write_readiness")
    if "audit" in text:
        return "configure_decision_audit_package"
    if "escalation" in text:
        return str(escalation_result.get("next_action_cta") or "review_escalation")
    if status == "escalated":
        return str(escalation_result.get("next_action_cta") or "review_escalation")
    return "review_pending_approval"


def _approval_action_type(action: str, record: Mapping[str, Any]) -> str:
    explicit = _normalize_key(record.get("action_type") or record.get("approval_review_action_type"))
    if explicit in APPROVAL_REVIEW_ACTION_TYPES:
        return explicit
    approval_type = _normalize_key(record.get("approval_type")) or approval_type_for_action(action, dict(record))
    for value in (approval_type, action):
        normalized = _normalize_key(value)
        if normalized in ACTION_TYPE_ALIASES:
            return ACTION_TYPE_ALIASES[normalized]
    if _truthy(record.get("workflow_promotion")) or action == "promote_workflow":
        return "workflow_promotion"
    return ACTION_TYPE_ALIASES.get(action, "campaign_launch")


def _customer_facing(record: Mapping[str, Any], action: str, action_type: str) -> bool:
    if "customer_facing" in record:
        return _truthy(record.get("customer_facing"))
    return action in CUSTOMER_FACING_WRITE_ACTIONS or action_type in CUSTOMER_FACING_ACTION_TYPES


def _external_write_required(record: Mapping[str, Any], action: str, action_type: str) -> bool:
    if "external_write_required" in record or "requires_external_write" in record:
        return _truthy(record.get("external_write_required") or record.get("requires_external_write"))
    if action_type == "workflow_promotion":
        return False
    return action in CUSTOMER_FACING_WRITE_ACTIONS or action_type in CUSTOMER_FACING_ACTION_TYPES


def _preview_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("preview_payload", "preview", "proposed_payload", "payload"):
        value = record.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {
        "summary": (
            _string_or_none(record.get("title") or record.get("summary"))
            or "Approval preview was not supplied."
        ),
        "payload_status": "missing_preview_payload",
    }


def _before_after_diff(record: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("before_after_diff", "diff", "diff_summary", "change_summary"):
        value = record.get(key)
        if isinstance(value, Mapping):
            return dict(value)
        if _string_or_none(value):
            return {"summary": str(value)}
    return {"summary": "No before/after diff was supplied."}


def _budget_impact(record: Mapping[str, Any]) -> dict[str, Any]:
    value = record.get("budget_impact")
    if isinstance(value, Mapping):
        return dict(value)
    return {
        "amount": _float_or_none(
            record.get("budget_delta")
            or record.get("budget_increase")
            or record.get("budget_amount")
            or record.get("daily_budget")
        ),
        "currency": _string_or_none(record.get("currency")) or "USD",
        "summary": _string_or_none(record.get("budget_impact_summary")) or "No budget impact supplied.",
    }


def _audience_impact(record: Mapping[str, Any]) -> dict[str, Any]:
    value = record.get("audience_impact") or record.get("list_impact")
    if isinstance(value, Mapping):
        return dict(value)
    return {
        "estimated_recipients": _float_or_none(
            record.get("audience_size")
            or record.get("list_size")
            or record.get("recipient_count")
            or record.get("estimated_recipients")
        ),
        "summary": _string_or_none(record.get("audience_impact_summary")) or "No audience/list impact supplied.",
    }


def _risk_flags(
    record: Mapping[str, Any],
    policy_result: Mapping[str, Any],
    escalation_result: Mapping[str, Any],
    timeout_result: Mapping[str, Any],
    write_readiness: Mapping[str, Any],
) -> list[str]:
    flags = _string_list(
        record.get("brand_legal_risk_flags")
        or record.get("risk_flags")
        or record.get("risks")
    )
    for key in ("brand_risk", "legal_risk", "pricing_claim", "high_risk_copy", "crisis_response"):
        if _truthy(record.get(key)):
            flags.append(key)
    for value in (
        policy_result.get("decision"),
        escalation_result.get("trigger_type"),
        timeout_result.get("outcome") if timeout_result.get("timed_out") else None,
        write_readiness.get("status") if write_readiness.get("status") == "unsafe" else None,
    ):
        if _string_or_none(value):
            flags.append(str(value))
    return _unique_strings(flags)


def _source_refs(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for key in ("source_refs", "source_references", "sources"):
        for item in _list_from_value(record.get(key)):
            refs.append(dict(item) if isinstance(item, Mapping) else {"ref": str(item)})
    return _unique_refs(refs)


def _connector_refs(
    record: Mapping[str, Any],
    connector_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in _list_from_value(record.get("connector_refs") or record.get("connector_references")):
        refs.append(dict(item) if isinstance(item, Mapping) else {"connector_key": str(item)})
    for key in _connector_keys(record):
        contract = _contract_for_key(connector_contracts, key)
        refs.append(
            {
                "connector_key": key,
                "type": "connector_contract",
                "write_status": (contract or {}).get("write_status"),
                "read_status": (contract or {}).get("read_status"),
                "contract_state": (contract or {}).get("contract_state"),
            }
        )
    return _unique_refs(refs)


def _connector_keys(record: Mapping[str, Any]) -> list[str]:
    keys = _string_list(
        record.get("connector_keys")
        or record.get("required_connector_keys")
        or record.get("connector_key")
        or record.get("connector")
    )
    for ref in _list_from_value(record.get("connector_refs") or record.get("connector_references")):
        if isinstance(ref, Mapping):
            keys.extend(_string_list(ref.get("connector_key") or ref.get("key")))
    return _unique_strings(keys)


def _contract_for_key(
    connector_contracts: list[dict[str, Any]],
    connector_key: str,
) -> dict[str, Any] | None:
    normalized = _normalize_key(connector_key)
    for row in connector_contracts:
        if _normalize_key(row.get("connector_key")) == normalized:
            return row
    return None


def _agent_rationale(record: Mapping[str, Any]) -> str:
    return _string_or_none(
        record.get("agent_rationale")
        or record.get("rationale")
        or record.get("recommendation_reason")
        or record.get("reason")
    ) or "Agent rationale was not supplied."


def _rollback_stop_plan(record: Mapping[str, Any], action_type: str) -> dict[str, Any]:
    value = record.get("rollback_stop_plan") or record.get("rollback_plan") or record.get("stop_plan")
    if isinstance(value, Mapping):
        return dict(value)
    defaults = {
        "campaign_launch": "Pause campaigns, revert budget changes, archive launch assets, and notify Marketing Ops.",
        "ad_budget_change": (
            "Restore previous budget cap and pause further spend mutations "
            "until reconciliation completes."
        ),
        "content_publish": "Unpublish or revert the CMS revision and keep the draft for legal/brand review.",
        "email_send": "Cancel scheduled sends, suppress affected segment, and notify lifecycle marketing.",
        "landing_page_change": "Rollback to the previous landing-page revision and invalidate published cache.",
        "target_account_list_change": "Restore the previous target account list and halt downstream ABM activation.",
        "crisis_public_response": (
            "Pause public response, escalate to comms/legal/CEO, and keep "
            "internal holding statement only."
        ),
        "high_risk_copy_pricing_legal_claim": "Block publish/send and route replacement copy to legal/compliance.",
        "workflow_promotion": "Keep workflow in shadow or pause mode and revoke active external-write permissions.",
    }
    return {
        "summary": defaults.get(action_type, "Pause workflow, stop external writes, and escalate to CMO."),
        "provided_by": "default_cmo_safety_plan",
    }


def _timeout_state(timeout_result: Mapping[str, Any]) -> str:
    if timeout_result.get("timed_out"):
        return "timed_out"
    return _normalize_key(timeout_result.get("status")) or "pending"


def _audit_refs(record: Mapping[str, Any]) -> list[str]:
    refs: list[Any] = [
        record.get("audit_ref"),
        record.get("audit_reference"),
        record.get("decision_audit_ref"),
        record.get("approval_audit_ref"),
    ]
    for key in ("audit_refs", "decision_audit_refs", "approval_audit_refs"):
        refs.extend(_list_from_value(record.get(key)))
    for key in ("audit_evidence", "decision_audit", "audit_package"):
        value = record.get(key)
        if isinstance(value, Mapping):
            refs.extend([value.get("audit_reference"), value.get("decision_audit_ref"), value.get("audit_id")])
    return _unique_strings(_strings(refs))


def _timeout_decisions_by_id(
    approval_timeout_risk: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    risk = approval_timeout_risk if isinstance(approval_timeout_risk, Mapping) else {}
    result: dict[str, dict[str, Any]] = {}
    for decision in _dicts(risk.get("approval_timeout_decisions")):
        approval_id = _approval_id(decision)
        if approval_id:
            result[approval_id] = decision
    return result


def _result_ref(result: Mapping[str, Any] | None, prefix: str) -> str | None:
    if not isinstance(result, Mapping) or not result:
        return None
    explicit = _string_or_none(
        result.get("audit_reference")
        or result.get("decision_audit_ref")
        or result.get("audit_id")
    )
    if explicit:
        return explicit
    return _stable_ref(prefix, result)


def _cta(action_key: Any) -> dict[str, str]:
    key = _normalize_key(action_key) or "none"
    return {
        "action_key": key,
        "label": CTA_LABELS.get(key, key.replace("_", " ").title()),
        "path": CTA_PATHS.get(key, "/dashboard/approvals"),
    }


def _review_needs_action(row: Mapping[str, Any]) -> bool:
    cta = row.get("next_action_cta")
    if isinstance(cta, Mapping):
        return _normalize_key(cta.get("action_key")) != "none"
    return _normalize_key(cta) not in {"", "none"}


CTA_LABELS = {
    "none": "No Action",
    "review_pending_approval": "Review Approval",
    "resolve_overdue_approvals": "Resolve Approval",
    "review_escalated_approval": "Review Escalation",
    "review_escalation": "Review Escalation",
    "configure_marketing_policy_manifest": "Configure Policy",
    "configure_decision_audit_package": "Configure Audit",
    "fix_connector_write_readiness": "Fix Write Readiness",
    "configure_connector_contract": "Configure Connector",
    "request_legal_or_compliance_review": "Request Legal Review",
    "request_budget_approval": "Review Budget",
    "request_marketing_approval": "Review Approval",
}

CTA_PATHS = {
    "none": "/dashboard/cmo",
    "review_pending_approval": "/dashboard/approvals",
    "resolve_overdue_approvals": "/dashboard/approvals",
    "review_escalated_approval": "/dashboard/approvals",
    "review_escalation": "/dashboard/approvals",
    "configure_marketing_policy_manifest": "/dashboard/settings",
    "configure_decision_audit_package": "/dashboard/audit",
    "fix_connector_write_readiness": "/dashboard/connectors",
    "configure_connector_contract": "/dashboard/connectors",
    "request_legal_or_compliance_review": "/dashboard/approvals",
    "request_budget_approval": "/dashboard/approvals",
    "request_marketing_approval": "/dashboard/approvals",
}


def _approval_id(record: Mapping[str, Any]) -> str | None:
    return _string_or_none(record.get("approval_id") or record.get("id") or record.get("hitl_id"))


def _approval_review_id(
    approval_id: str,
    action: str,
    workflow_run_id: Any,
    step_id: Any,
) -> str:
    source = "|".join(
        str(part or "")
        for part in (approval_id, action, workflow_run_id, step_id)
    )
    return f"cmo_approval_review_{hashlib.sha256(source.encode()).hexdigest()[:20]}"


def _work_queue_item_id(approval_id: str) -> str:
    return f"cmo_wq_{hashlib.sha256(f'approval:{approval_id}'.encode()).hexdigest()[:20]}"


def _stable_ref(prefix: str, *parts: Any) -> str:
    encoded = "|".join(str(part or "") for part in parts)
    return f"{prefix}:{hashlib.sha256(encoded.encode()).hexdigest()[:20]}"


def _stable_hash(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}_{hashlib.sha256(repr(sorted(value.items())).encode()).hexdigest()[:16]}"


def _status_sort(status: str) -> int:
    return {
        "blocked": 0,
        "timed_out": 1,
        "escalated": 2,
        "pending": 3,
        "overridden": 4,
        "rejected": 5,
        "approved": 6,
    }.get(status, 9)


def _datetime_string(value: Any, fallback: datetime | None) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is not None:
        return parsed.isoformat()
    return fallback.isoformat() if fallback is not None else None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if isinstance(value, str) and value.strip():
        try:
            return _ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _float_or_none(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, str) or value is None:
        return []
    if isinstance(value, Iterable):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _list_from_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return list(value)
    return [value]


def _strings(values: Iterable[Any]) -> list[str]:
    return [str(value) for value in values if _string_or_none(value)]


def _string_list(value: Any) -> list[str]:
    return _strings(_list_from_value(value))


def _unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _strings(values):
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _unique_refs(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for value in values:
        encoded = repr(sorted(dict(value).items()))
        if encoded in seen:
            continue
        seen.add(encoded)
        result.append(dict(value))
    return result


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
