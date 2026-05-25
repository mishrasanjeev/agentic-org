"""Marketing external-write final-state policy.

CMO-5.3 turns connector write confirmation from a dashboard contract into
workflow-step behavior. This module does not call vendor APIs. It normalizes
the result that a connector/agent reports after an attempted marketing write,
checks idempotency and prior confirmations, and emits structured audit evidence
that the workflow result can persist.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from core.marketing.approval_timeouts import approval_timeout_allows_external_write
from core.marketing.connector_contracts import plan_marketing_write_retry
from core.marketing.decision_audit import build_external_write_decision_audit
from core.marketing.escalation_matrix import evaluate_marketing_escalation
from core.marketing.policy_manifest import (
    evaluate_marketing_policy,
    marketing_policy_allows_external_write,
)

WRITE_FINAL_STATES = (
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

INTERNAL_ONLY_MODES = {"draft", "internal", "internal_only", "recommendation", "shadow", "simulation"}
# enterprise-gate: process-local-ok reason=static-write-state-set
EXTERNAL_SUCCESS_STATES = {"write_confirmed", "idempotent_recovered"}
# enterprise-gate: process-local-ok reason=static-write-state-set
INTERNAL_SUCCESS_STATES = {"draft_created", "shadow_only"}
# enterprise-gate: process-local-ok reason=static-write-state-set
UNCONFIRMED_STATES = {"accepted", "write_unconfirmed", "timeout_unknown"}
EXTERNAL_CONFIRMATION_FIELDS = {
    "external_object_id",
    "source_object_id",
    "external_id",
    "connector_object_id",
}
EXTERNAL_CONFIRMATION_PAYLOAD_FIELDS = {
    *EXTERNAL_CONFIRMATION_FIELDS,
    "id",
    "campaign_id",
    "message_id",
    "post_id",
    "crm_record_id",
}
INTERNAL_SIGNAL_FIELDS = {
    "approval_id",
    "draft_id",
    "draft_url",
    "internal_record_id",
    "recommendation",
    "simulation",
    "simulation_result",
}


def evaluate_marketing_external_write_result(
    connector_contracts: Iterable[dict[str, Any]],
    *,
    connector_key: str | None,
    action: str,
    workflow_mode: str,
    output: dict[str, Any],
    step: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate whether a marketing write step can complete.

    The returned ``step_status`` is one of the workflow engine's native step
    statuses. ``completed`` means either a confirmed external write or a
    clearly internal/draft/shadow result. ``waiting_delay`` means a safe
    idempotent retry was scheduled. ``failed`` means the active workflow must
    fail closed.
    """

    step = step or {}
    state = state or {}
    output = output if isinstance(output, dict) else {}
    now = _ensure_aware(now) or datetime.now(UTC)
    mode = _normalize_key(workflow_mode) or "active"
    normalized_action = _normalize_key(action)
    normalized_connector = _normalize_connector(connector_key, output, step)
    attempt = _attempt_metadata(
        normalized_connector,
        normalized_action,
        output,
        step,
        state,
        now,
    )
    reported_state = _reported_write_state(output)
    idempotency_key = attempt["idempotency_key"]
    prior_confirmation = _prior_confirmation(
        connector_contracts,
        normalized_connector,
        normalized_action,
        idempotency_key,
    )

    if mode in INTERNAL_ONLY_MODES:
        return _evaluate_internal_mode(
            mode,
            reported_state,
            prior_confirmation,
            attempt,
            output,
            now,
        )

    timeout_decision = _approval_timeout_decision(output, step, state)
    if timeout_decision and not approval_timeout_allows_external_write(timeout_decision):
        return _decision(
            step_status="failed",
            final_state="write_unconfirmed",
            reason=(
                "Approval timed out and policy does not allow customer-facing "
                "external writes after timeout."
            ),
            next_action=str(timeout_decision.get("next_action_cta") or "resolve_overdue_approval"),
            attempt=attempt,
            confirmation=None,
            now=now,
            error_code="external_write_approval_timeout",
        )

    policy_decision = _marketing_policy_decision(
        output,
        step,
        state,
        normalized_action,
        mode,
    )
    write_safety = _connector_write_safety(connector_contracts, normalized_connector)
    if not write_safety["safe"]:
        return _decision(
            step_status="failed",
            final_state="write_unconfirmed",
            reason=str(write_safety["reason"]),
            next_action=str(write_safety["next_action"]),
            attempt=attempt,
            confirmation=None,
            now=now,
            error_code="external_write_connector_not_write_safe",
            policy_decision=policy_decision,
        )

    if not marketing_policy_allows_external_write(
        policy_decision,
        approval_satisfied=_approval_satisfied(output, step, state),
    ):
        return _decision(
            step_status="failed",
            final_state="write_unconfirmed",
            reason=_policy_failure_reason(policy_decision),
            next_action=str(policy_decision.get("next_action_cta") or "resolve_marketing_policy"),
            attempt=attempt,
            confirmation=None,
            now=now,
            error_code=_policy_error_code(policy_decision),
            policy_decision=policy_decision,
        )

    if prior_confirmation:
        confirmation = _confirmation_from_prior(prior_confirmation, attempt, now)
        return _decision(
            step_status="completed",
            final_state="idempotent_recovered",
            reason="Existing external write confirmation matched this idempotency key; no duplicate write is needed.",
            next_action="none",
            attempt=attempt,
            confirmation=confirmation,
            now=now,
            policy_decision=policy_decision,
        )

    if reported_state == "rejected":
        return _decision(
            step_status="failed",
            final_state="rejected",
            reason=_rejection_reason(output),
            next_action=_next_action(output, "fix_and_resubmit"),
            attempt=attempt,
            confirmation=None,
            now=now,
            policy_decision=policy_decision,
        )

    if reported_state == "timeout_unknown":
        retry_plan = _retry_plan(connector_contracts, normalized_connector, normalized_action, idempotency_key)
        if retry_plan.get("safe_to_retry"):
            retry_at = _string_or_none(retry_plan.get("next_retry_at"))
            return _decision(
                step_status="waiting_delay",
                final_state="retry_scheduled",
                reason=(
                    "Connector timed out with unknown write state; retry is scheduled "
                    "with the same idempotency key."
                ),
                next_action="wait_for_idempotent_retry",
                attempt=attempt,
                confirmation=None,
                retry_plan=retry_plan,
                resume_at=retry_at,
                now=now,
                policy_decision=policy_decision,
            )
        return _decision(
            step_status="failed",
            final_state="timeout_unknown",
            reason=str(retry_plan.get("reason") or "Connector timed out and safe retry metadata is missing."),
            next_action="manual_reconcile_before_retry",
            attempt=attempt,
            confirmation=None,
            retry_plan=retry_plan,
            now=now,
            policy_decision=policy_decision,
        )

    if reported_state == "retry_scheduled":
        retry_plan = _retry_plan(connector_contracts, normalized_connector, normalized_action, idempotency_key)
        if retry_plan.get("safe_to_retry"):
            retry_at = _string_or_none(output.get("next_retry_at") or retry_plan.get("next_retry_at"))
            return _decision(
                step_status="waiting_delay",
                final_state="retry_scheduled",
                reason="Connector scheduled a safe retry with idempotency metadata.",
                next_action="wait_for_idempotent_retry",
                attempt=attempt,
                confirmation=None,
                retry_plan=retry_plan,
                resume_at=retry_at,
                now=now,
                policy_decision=policy_decision,
            )
        return _decision(
            step_status="failed",
            final_state="write_unconfirmed",
            reason=str(retry_plan.get("reason") or "Retry was requested without safe idempotency metadata."),
            next_action="manual_reconcile_before_retry",
            attempt=attempt,
            confirmation=None,
            retry_plan=retry_plan,
            now=now,
            error_code="external_write_retry_blocked",
            policy_decision=policy_decision,
        )

    if reported_state == "draft_created":
        return _decision(
            step_status="failed",
            final_state="draft_created",
            reason=(
                "Active external-write step created only a draft; it cannot be reported "
                "as published, sent, launched, or updated."
            ),
            next_action="promote_draft_or_mark_step_internal",
            attempt=attempt,
            confirmation=None,
            now=now,
            policy_decision=policy_decision,
        )

    if reported_state == "write_confirmed" or (
        reported_state == "accepted" and _has_external_object_id(output)
    ):
        confirmation = _confirmation_from_output(output, attempt, now)
        if confirmation.get("external_object_id"):
            return _decision(
                step_status="completed",
                final_state="write_confirmed",
                reason="Connector accepted and confirmed the external write.",
                next_action="none",
                attempt=attempt,
                confirmation=confirmation,
                now=now,
                policy_decision=policy_decision,
            )

    return _decision(
        step_status="failed",
        final_state="write_unconfirmed",
        reason="Active external-write step did not include connector confirmation with an external object ID.",
        next_action="confirm_write_or_reconcile",
        attempt=attempt,
        confirmation=None,
        now=now,
        policy_decision=policy_decision,
    )


def _evaluate_internal_mode(
    mode: str,
    reported_state: str,
    prior_confirmation: dict[str, Any] | None,
    attempt: dict[str, Any],
    output: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    if prior_confirmation or reported_state in {"accepted", "write_confirmed"} or _has_external_object_id(output):
        return _decision(
            step_status="failed",
            final_state="write_unconfirmed",
            reason="Draft, shadow, and internal-only workflows must not execute external marketing writes.",
            next_action="remove_external_write_from_shadow_step",
            attempt=attempt,
            confirmation=None,
            now=now,
            error_code="external_write_shadow_violation" if mode == "shadow" else "external_write_internal_violation",
        )

    if mode == "shadow":
        final_state = "shadow_only"
        reason = (
            "Shadow workflow produced a recommendation, draft, simulation, or approval "
            "record without an external write."
        )
    elif _is_clearly_internal(output, reported_state):
        final_state = (
            "draft_created"
            if reported_state == "draft_created" or _has_any(output, {"draft_id", "draft_url"})
            else "shadow_only"
        )
        reason = "Draft/internal-only workflow step is clearly labeled as non-external."
    else:
        return _decision(
            step_status="failed",
            final_state="write_unconfirmed",
            reason=(
                "Draft/internal-only step is not clearly labeled as draft, simulation, "
                "recommendation, or internal approval."
            ),
            next_action="label_step_as_draft_or_internal",
            attempt=attempt,
            confirmation=None,
            now=now,
            error_code="external_write_draft_label_missing",
        )

    return _decision(
        step_status="completed",
        final_state=final_state,
        reason=reason,
        next_action="none",
        attempt=attempt,
        confirmation=None,
        now=now,
    )


def _decision(
    *,
    step_status: str,
    final_state: str,
    reason: str,
    next_action: str,
    attempt: dict[str, Any],
    confirmation: dict[str, Any] | None,
    now: datetime,
    retry_plan: dict[str, Any] | None = None,
    resume_at: str | None = None,
    error_code: str | None = None,
    policy_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audit_events = _audit_events(final_state, attempt, confirmation, reason, now, retry_plan)
    resolved_error_code = error_code or _error_code_for(final_state, step_status)
    escalation_decision = _write_escalation_decision(
        resolved_error_code,
        final_state,
        step_status,
        reason,
        attempt,
        policy_decision,
        now,
    )
    decision = {
        "step_status": step_status,
        "final_state": final_state,
        "can_mark_complete": step_status == "completed",
        "reason": reason,
        "next_action": next_action,
        "attempt": attempt,
        "confirmation": confirmation,
        "retry_plan": retry_plan,
        "resume_at": resume_at,
        "audit_events": audit_events,
        "audit_reference": audit_events[-1]["audit_reference"],
        "error_code": resolved_error_code,
        "marketing_policy_decision": policy_decision,
        "escalation_decision": escalation_decision,
        "escalation_evidence": escalation_decision.get("evidence") if escalation_decision else None,
    }
    decision["decision_audit"] = audit_events[-1].get("decision_audit")
    decision["decision_audit_ref"] = audit_events[-1].get("decision_audit_ref")
    return decision


def _write_escalation_decision(
    error_code: str | None,
    final_state: str,
    step_status: str,
    reason: str,
    attempt: dict[str, Any],
    policy_decision: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any] | None:
    trigger_type = _write_escalation_trigger(error_code, final_state, step_status)
    if trigger_type is None:
        return None
    if (
        trigger_type in {"approval_timeout", "missing_policy"}
        or str(error_code or "").startswith("external_write_marketing_policy")
    ) and isinstance(policy_decision, dict):
        existing = policy_decision.get("escalation_decision")
        if isinstance(existing, dict) and existing.get("decision") != "no_escalation":
            return existing
    return evaluate_marketing_escalation(
        {
            "trigger_type": trigger_type,
            "connector_key": attempt.get("connector_key"),
            "action": attempt.get("action"),
            "workflow_id": attempt.get("workflow_id"),
            "workflow_run_id": attempt.get("workflow_run_id"),
            "run_id": attempt.get("run_id"),
            "step_id": attempt.get("step_id"),
            "severity": "critical" if trigger_type == "external_write_timeout_unknown" else "high",
            "reason": reason,
        },
        now=now,
    )


def _write_escalation_trigger(
    error_code: str | None,
    final_state: str,
    step_status: str,
) -> str | None:
    if step_status != "failed" and final_state != "retry_scheduled":
        return None
    if error_code == "external_write_approval_timeout":
        return "approval_timeout"
    if error_code == "external_write_marketing_policy_missing":
        return "missing_policy"
    if error_code == "external_write_connector_not_write_safe":
        return "connector_degraded"
    if final_state == "rejected" or error_code == "external_write_rejected":
        return "external_write_rejected"
    if final_state == "timeout_unknown" or error_code == "external_write_timeout_unknown":
        return "external_write_timeout_unknown"
    if final_state == "write_unconfirmed":
        return "external_write_timeout_unknown"
    return None


def _attempt_metadata(
    connector_key: str | None,
    action: str,
    output: dict[str, Any],
    step: dict[str, Any],
    state: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    idempotency_key = _idempotency_key(output, step)
    fingerprint = _request_fingerprint(output, step, connector_key, action, idempotency_key)
    workflow_run_id = _string_or_none(
        state.get("workflow_run_id")
        or state.get("db_workflow_run_id")
        or state.get("database_workflow_run_id")
        or state.get("id")
    )
    agent_id = _string_or_none(output.get("agent_id") or step.get("agent_id") or step.get("agent"))
    actor_id = _string_or_none(output.get("actor_id") or agent_id or workflow_run_id)
    audit_reference = _audit_reference("attempt", connector_key, action, fingerprint, idempotency_key)
    return {
        "connector_key": connector_key,
        "action": action,
        "idempotency_key": idempotency_key,
        "request_fingerprint": fingerprint,
        "attempted_at": now.isoformat(),
        "actor_id": actor_id,
        "actor_type": _string_or_none(output.get("actor_type")) or "agent",
        "agent_id": agent_id,
        "workflow_id": _string_or_none(state.get("workflow_id") or state.get("definition_id")),
        "workflow_run_id": workflow_run_id,
        "run_id": _string_or_none(state.get("id")),
        "tenant_id": _string_or_none(state.get("tenant_id")),
        "step_id": _string_or_none(step.get("id")),
        "audit_reference": audit_reference,
    }


def _confirmation_from_output(
    output: dict[str, Any],
    attempt: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    confirmation_payload = _confirmation_payload(output)
    confirmed_at = _string_or_none(
        confirmation_payload.get("confirmed_at")
        or output.get("confirmed_at")
        or output.get("external_write_confirmed_at")
    ) or now.isoformat()
    external_object_id = _external_object_id(output)
    source_url = _string_or_none(
        confirmation_payload.get("source_url")
        or confirmation_payload.get("url")
        or output.get("source_url")
        or output.get("external_object_url")
    )
    return {
        "connector_key": attempt.get("connector_key"),
        "action": attempt.get("action"),
        "status": "write_confirmed",
        "external_object_id": external_object_id,
        "source_url": source_url,
        "idempotency_key": attempt.get("idempotency_key"),
        "request_fingerprint": attempt.get("request_fingerprint"),
        "confirmed_at": confirmed_at,
        "actor_id": attempt.get("actor_id"),
        "actor_type": attempt.get("actor_type"),
        "agent_id": attempt.get("agent_id"),
        "workflow_id": attempt.get("workflow_id"),
        "workflow_run_id": attempt.get("workflow_run_id"),
        "run_id": attempt.get("run_id"),
        "audit_reference": _audit_reference(
            "confirmed",
            attempt.get("connector_key"),
            attempt.get("action"),
            attempt.get("request_fingerprint"),
            attempt.get("idempotency_key"),
        ),
    }


def _confirmation_from_prior(
    prior: dict[str, Any],
    attempt: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    return {
        "connector_key": attempt.get("connector_key"),
        "action": attempt.get("action"),
        "status": "idempotent_recovered",
        "external_object_id": prior.get("external_object_id"),
        "source_url": prior.get("source_url"),
        "idempotency_key": prior.get("idempotency_key") or attempt.get("idempotency_key"),
        "request_fingerprint": prior.get("request_fingerprint") or attempt.get("request_fingerprint"),
        "confirmed_at": prior.get("confirmed_at") or now.isoformat(),
        "actor_id": attempt.get("actor_id"),
        "actor_type": attempt.get("actor_type"),
        "agent_id": attempt.get("agent_id"),
        "workflow_id": attempt.get("workflow_id"),
        "workflow_run_id": attempt.get("workflow_run_id"),
        "run_id": attempt.get("run_id"),
        "audit_reference": _audit_reference(
            "idempotent_recovered",
            attempt.get("connector_key"),
            attempt.get("action"),
            attempt.get("request_fingerprint"),
            prior.get("idempotency_key") or attempt.get("idempotency_key"),
        ),
    }


def _audit_events(
    final_state: str,
    attempt: dict[str, Any],
    confirmation: dict[str, Any] | None,
    reason: str,
    now: datetime,
    retry_plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    attempted = {
        "event_type": "marketing_external_write_attempted",
        "outcome": "attempted",
        "audit_reference": attempt["audit_reference"],
        "connector_key": attempt.get("connector_key"),
        "action": attempt.get("action"),
        "idempotency_key": attempt.get("idempotency_key"),
        "request_fingerprint": attempt.get("request_fingerprint"),
        "actor_id": attempt.get("actor_id"),
        "agent_id": attempt.get("agent_id"),
        "workflow_run_id": attempt.get("workflow_run_id"),
        "workflow_id": attempt.get("workflow_id"),
        "run_id": attempt.get("run_id"),
        "step_id": attempt.get("step_id"),
        "created_at": attempt.get("attempted_at"),
    }
    attempted_package = build_external_write_decision_audit(
        attempted,
        event_type="external_write_attempt",
        now=_ensure_aware(now),
    )
    attempted["decision_audit"] = attempted_package
    attempted["decision_audit_ref"] = attempted_package["audit_reference"]
    final = {
        "event_type": f"marketing_external_write_{final_state}",
        "outcome": final_state,
        "audit_reference": _audit_reference(
            final_state,
            attempt.get("connector_key"),
            attempt.get("action"),
            attempt.get("request_fingerprint"),
            attempt.get("idempotency_key"),
        ),
        "connector_key": attempt.get("connector_key"),
        "action": attempt.get("action"),
        "idempotency_key": attempt.get("idempotency_key"),
        "request_fingerprint": attempt.get("request_fingerprint"),
        "reason": reason,
        "next_retry_at": (retry_plan or {}).get("next_retry_at"),
        "external_object_id": (confirmation or {}).get("external_object_id"),
        "source_url": (confirmation or {}).get("source_url"),
        "actor_id": attempt.get("actor_id"),
        "actor_type": attempt.get("actor_type"),
        "agent_id": attempt.get("agent_id"),
        "workflow_run_id": attempt.get("workflow_run_id"),
        "workflow_id": attempt.get("workflow_id"),
        "run_id": attempt.get("run_id"),
        "step_id": attempt.get("step_id"),
        "created_at": now.isoformat(),
    }
    final_package = build_external_write_decision_audit(final, now=_ensure_aware(now))
    final["decision_audit"] = final_package
    final["decision_audit_ref"] = final_package["audit_reference"]
    return [attempted, final]


def _prior_confirmation(
    connector_contracts: Iterable[dict[str, Any]],
    connector_key: str | None,
    action: str,
    idempotency_key: str | None,
) -> dict[str, Any] | None:
    if not connector_key or not idempotency_key:
        return None
    normalized_connector = _normalize_key(connector_key)
    normalized_action = _normalize_key(action)
    for row in connector_contracts:
        if _normalize_key(row.get("connector_key")) != normalized_connector:
            continue
        confirmations = row.get("external_write_confirmations") or []
        if not isinstance(confirmations, list):
            continue
        for confirmation in confirmations:
            if not isinstance(confirmation, dict):
                continue
            if _normalize_key(confirmation.get("action")) != normalized_action:
                continue
            if idempotency_key and confirmation.get("idempotency_key") != idempotency_key:
                continue
            if confirmation.get("status") == "write_confirmed":
                return confirmation
    return None


def _retry_plan(
    connector_contracts: Iterable[dict[str, Any]],
    connector_key: str | None,
    action: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    if not connector_key:
        return {
            "safe_to_retry": False,
            "reason": "Connector key is missing; retry cannot be tied to idempotency metadata.",
            "duplicate_policy": "blocked_without_connector",
        }
    return plan_marketing_write_retry(
        connector_contracts,
        connector_key,
        action,
        idempotency_key=idempotency_key,
    )


def _connector_write_safety(
    connector_contracts: Iterable[dict[str, Any]],
    connector_key: str | None,
) -> dict[str, Any]:
    if not connector_key:
        return {
            "safe": False,
            "reason": "Connector key is missing; active external write cannot prove write safety.",
            "next_action": "configure_connector_contract",
        }
    normalized_connector = _normalize_key(connector_key)
    saw_contract = False
    for row in connector_contracts:
        saw_contract = True
        if _normalize_key(row.get("connector_key")) != normalized_connector:
            continue
        degraded_mode = row.get("degraded_mode") if isinstance(row.get("degraded_mode"), dict) else {}
        retry_policy = row.get("retry_policy") if isinstance(row.get("retry_policy"), dict) else {}
        if row.get("blocks_external_writes") or retry_policy.get("blocks_external_writes"):
            return {
                "safe": False,
                "reason": (
                    degraded_mode.get("reason")
                    or row.get("degraded_mode_reason")
                    or f"Connector failure class {row.get('failure_class') or 'unknown'} blocks external writes."
                ),
                "next_action": degraded_mode.get("next_action_cta") or row.get("next_action_cta") or "fix_connector",
            }
        if "write_safe" in row:
            safe = bool(row.get("write_safe"))
        elif "write_ready" in row:
            safe = bool(row.get("write_ready"))
        else:
            safe = True
        return {
            "safe": safe,
            "reason": (
                "Connector contract is write-safe."
                if safe
                else row.get("degraded_mode_reason") or "Connector contract is not write-ready."
            ),
            "next_action": row.get("next_action_cta") or "fix_connector",
        }
    if not saw_contract:
        return {
            "safe": True,
            "reason": (
                "No connector contract context was supplied; write completion "
                "still requires explicit confirmation."
            ),
            "next_action": "confirm_write_or_reconcile",
        }
    return {
        "safe": False,
        "reason": "Connector contract is unavailable; active external write cannot prove write safety.",
        "next_action": "configure_connector_contract",
    }


def _approval_timeout_decision(
    output: dict[str, Any],
    step: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    for container in (
        output,
        step,
        state,
        state.get("context") if isinstance(state.get("context"), dict) else {},
        state.get("trigger_payload") if isinstance(state.get("trigger_payload"), dict) else {},
    ):
        if not isinstance(container, dict):
            continue
        value = container.get("approval_timeout_decision") or container.get("cmo_approval_timeout_decision")
        if isinstance(value, dict):
            return value
    return None


def _marketing_policy_decision(
    output: dict[str, Any],
    step: dict[str, Any],
    state: dict[str, Any],
    action: str,
    mode: str,
) -> dict[str, Any]:
    for container in _policy_containers(output, step, state):
        value = (
            container.get("marketing_policy_decision")
            or container.get("cmo_marketing_policy_decision")
        )
        if isinstance(value, dict):
            return value

    manifest = _marketing_policy_manifest(output, step, state)
    context: dict[str, Any] = {}
    for container in reversed(_policy_containers(output, step, state)):
        context.update(container)
    context.update(
        {
            "action": action,
            "workflow_mode": mode,
            "workflow_id": (
                state.get("workflow_id")
                or state.get("definition_id")
                or step.get("workflow_id")
                or output.get("workflow_id")
            ),
            "external_write_required": True,
            "customer_facing": True,
            "connector_key": (
                output.get("connector_key")
                or step.get("connector_key")
                or output.get("connector")
                or step.get("connector")
            ),
        }
    )
    return evaluate_marketing_policy(context, manifest=manifest, use_default=manifest is None)


def _marketing_policy_manifest(
    output: dict[str, Any],
    step: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    for container in _policy_containers(output, step, state):
        for key in (
            "marketing_policy_manifest",
            "cmo_marketing_policy_manifest",
            "policy_manifest",
        ):
            value = container.get(key)
            if isinstance(value, dict):
                return value
    return None


def _policy_containers(
    output: dict[str, Any],
    step: dict[str, Any],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        container
        for container in (
            output,
            step,
            state,
            state.get("context") if isinstance(state.get("context"), dict) else {},
            state.get("trigger_payload") if isinstance(state.get("trigger_payload"), dict) else {},
        )
        if isinstance(container, dict)
    ]


def _approval_satisfied(
    output: dict[str, Any],
    step: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    for container in _policy_containers(output, step, state):
        if container.get("marketing_policy_approval_satisfied") is True:
            return True
        if container.get("approval_satisfied") is True or container.get("approved") is True:
            return True
        timeout_decision = container.get("approval_timeout_decision") or container.get("cmo_approval_timeout_decision")
        if isinstance(timeout_decision, dict) and timeout_decision.get("external_writes_allowed") is True:
            return True
        status = _normalize_key(container.get("approval_status") or container.get("policy_approval_status"))
        if status in {"approved", "accepted"}:
            return True
        for key in ("approval_decision", "human_approval", "policy_approval"):
            value = container.get(key)
            if not isinstance(value, dict):
                continue
            nested_status = _normalize_key(value.get("status") or value.get("decision"))
            if nested_status in {"approved", "accepted"}:
                return True
            if _string_or_none(value.get("approved_by") or value.get("decision_by")):
                return True
    return False


def _policy_failure_reason(policy_decision: dict[str, Any]) -> str:
    decision = str(policy_decision.get("decision") or "")
    if decision == "missing_policy":
        return "Active customer-facing marketing write has no matching policy manifest rule."
    if decision == "blocked":
        return str(policy_decision.get("reason") or "Marketing policy blocks this external write.")
    if decision == "read_only_only":
        return "Marketing policy permits this workflow only as read-only/shadow output."
    if decision in {"requires_approval", "requires_escalation"}:
        return (
            "Marketing policy requires approval or escalation before this "
            "customer-facing external write can complete."
        )
    return "Marketing policy does not allow this external write."


def _policy_error_code(policy_decision: dict[str, Any]) -> str:
    decision = str(policy_decision.get("decision") or "")
    if decision == "missing_policy":
        return "external_write_marketing_policy_missing"
    if decision == "blocked":
        return "external_write_marketing_policy_blocked"
    if decision == "read_only_only":
        return "external_write_marketing_policy_read_only"
    if decision in {"requires_approval", "requires_escalation"}:
        return "external_write_marketing_policy_approval_required"
    return "external_write_marketing_policy_denied"


def _reported_write_state(output: dict[str, Any]) -> str:
    value = _normalize_key(
        output.get("external_write_state")
        or output.get("write_state")
        or output.get("external_write_confirmation_status")
        or output.get("write_confirmation_status")
        or output.get("connector_write_status")
        or output.get("status")
    )
    if value in WRITE_FINAL_STATES:
        return value
    if value in {"confirmed", "success", "succeeded"}:
        return "write_confirmed"
    if value in {"accepted_pending_confirmation", "accepted_pending"}:
        return "accepted"
    if value in {"timed_out", "timeout", "unknown"}:
        return "timeout_unknown"
    if value in {"failed", "denied"}:
        return "rejected"
    if _has_external_object_id(output):
        return "accepted"
    return "write_unconfirmed"


def _confirmation_payload(output: dict[str, Any]) -> dict[str, Any]:
    raw = (
        output.get("external_write_confirmation")
        or output.get("write_confirmation")
        or output.get("connector_write_confirmation")
        or {}
    )
    return raw if isinstance(raw, dict) else {}


def _external_object_id(output: dict[str, Any]) -> str | None:
    confirmation = _confirmation_payload(output)
    for field in EXTERNAL_CONFIRMATION_PAYLOAD_FIELDS:
        value = _string_or_none(confirmation.get(field))
        if value:
            return value
    for field in EXTERNAL_CONFIRMATION_FIELDS:
        value = _string_or_none(output.get(field))
        if value:
            return value
    return None


def _has_external_object_id(output: dict[str, Any]) -> bool:
    return _external_object_id(output) is not None


def _has_any(output: dict[str, Any], fields: set[str]) -> bool:
    return any(_string_or_none(output.get(field)) for field in fields)


def _is_clearly_internal(output: dict[str, Any], reported_state: str) -> bool:
    return (
        reported_state in INTERNAL_SUCCESS_STATES
        or _has_any(output, INTERNAL_SIGNAL_FIELDS)
        or bool(output.get("internal_only") is True or output.get("shadow_only") is True)
    )


def _idempotency_key(output: dict[str, Any], step: dict[str, Any]) -> str | None:
    confirmation = _confirmation_payload(output)
    return _string_or_none(
        output.get("idempotency_key")
        or output.get("external_write_idempotency_key")
        or confirmation.get("idempotency_key")
        or step.get("idempotency_key")
    )


def _request_fingerprint(
    output: dict[str, Any],
    step: dict[str, Any],
    connector_key: str | None,
    action: str,
    idempotency_key: str | None,
) -> str:
    explicit = _string_or_none(
        output.get("request_fingerprint")
        or output.get("request_hash")
        or output.get("external_write_request_fingerprint")
    )
    if explicit:
        return explicit
    payload = {
        "action": action,
        "connector_key": connector_key,
        "idempotency_key": idempotency_key,
        "inputs": step.get("inputs") or {},
        "request": output.get("external_write_request") or output.get("write_request") or {},
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _audit_reference(
    label: str,
    connector_key: str | None,
    action: str | None,
    request_fingerprint: str | None,
    idempotency_key: str | None,
) -> str:
    source = "|".join(
        str(item or "")
        for item in (label, connector_key, action, request_fingerprint, idempotency_key)
    )
    return f"mkt_write_{hashlib.sha256(source.encode()).hexdigest()[:20]}"


def _rejection_reason(output: dict[str, Any]) -> str:
    return _string_or_none(
        output.get("rejection_reason")
        or output.get("error")
        or output.get("reason")
        or output.get("message")
    ) or "Connector rejected the external write."


def _next_action(output: dict[str, Any], default: str) -> str:
    return _normalize_key(output.get("next_action") or output.get("next_action_cta") or default)


def _error_code_for(final_state: str, step_status: str) -> str | None:
    if step_status != "failed":
        return None
    if final_state == "rejected":
        return "external_write_rejected"
    if final_state == "timeout_unknown":
        return "external_write_timeout_unknown"
    if final_state == "draft_created":
        return "external_write_draft_only"
    return "external_write_confirmation_missing"


def _normalize_connector(
    connector_key: str | None,
    output: dict[str, Any],
    step: dict[str, Any],
) -> str | None:
    return _string_or_none(
        connector_key
        or output.get("connector_key")
        or output.get("connector")
        or step.get("connector_key")
        or step.get("connector")
    )


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
