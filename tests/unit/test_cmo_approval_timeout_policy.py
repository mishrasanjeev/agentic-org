from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.marketing.approval_timeouts import (
    build_approval_timeout_risk,
    build_workflow_approval_timeout_status,
    evaluate_approval_timeout,
)
from core.marketing.external_writes import evaluate_marketing_external_write_result
from core.marketing.workflow_linter import lint_marketing_workflow

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def _approval(**overrides: object) -> dict:
    approval = {
        "approval_id": "apr_campaign_1",
        "workflow_id": "campaign_launch",
        "workflow_run_id": "run_campaign_1",
        "step_id": "launch_ads",
        "action": "launch_campaign",
        "requested_approver": "cmo@example.com",
        "requested_approver_role": "cmo",
        "created_at": NOW - timedelta(hours=5),
        "due_at": NOW - timedelta(hours=1),
        "status": "pending",
    }
    approval.update(overrides)
    return approval


def _contract() -> dict:
    return {
        "connector_key": "google_ads",
        "write_safe": True,
        "idempotency_key_supported": True,
        "retry_budget": {
            "max_attempts": 3,
            "attempts_used": 0,
            "remaining_attempts": 3,
            "idempotency_key": "ads-launch-1",
        },
        "external_write_confirmations": [],
    }


def _confirmed_output(decision: dict) -> dict:
    return {
        "external_write_state": "write_confirmed",
        "external_object_id": "customers/123/campaigns/456",
        "source_url": "https://ads.google.com/campaigns/456",
        "idempotency_key": "ads-launch-1",
        "request_fingerprint": "fp-launch-1",
        "approval_timeout_decision": decision,
    }


def _workflow(step: dict) -> dict:
    return {
        "id": "wf_timeout",
        "name": "Timeout Workflow",
        "domain": "marketing",
        "mode": "production",
        "steps": [step],
    }


def test_pending_approval_before_sla_is_not_timed_out() -> None:
    decision = evaluate_approval_timeout(
        _approval(created_at=NOW - timedelta(minutes=30), due_at=NOW + timedelta(hours=1)),
        now=NOW,
    )

    assert decision["status"] == "pending"
    assert decision["timed_out"] is False
    assert decision["workflow_state"] == "waiting_hitl"
    assert decision["external_writes_allowed"] is False
    assert decision["audit_evidence"] is None


def test_approval_after_due_at_triggers_timeout_evaluation() -> None:
    decision = evaluate_approval_timeout(_approval(), now=NOW)

    assert decision["timed_out"] is True
    assert decision["status"] == "timed_out"
    assert decision["timed_out_at"] == NOW.isoformat()
    assert decision["outcome"] == "auto_cancel"
    assert decision["next_action_cta"] == "restart_approval"


def test_default_timeout_outcome_fails_closed_for_external_writes() -> None:
    decision = evaluate_approval_timeout(_approval(), now=NOW)

    assert decision["external_writes_allowed"] is False
    assert decision["workflow_state"] == "cancelled"
    assert decision["audit_evidence"]["external_writes_allowed"] is False
    assert decision["audit_evidence"]["blocked_action"] == "launch_campaign"


def test_auto_cancel_blocks_step_and_creates_audit_evidence() -> None:
    decision = evaluate_approval_timeout(_approval(), now=NOW)

    assert decision["outcome"] == "auto_cancel"
    assert decision["workflow_state"] == "cancelled"
    assert decision["step_status"] == "cancelled"
    assert decision["progress_allowed"] is False
    audit = decision["audit_evidence"]
    assert audit["event_type"] == "cmo_approval_timeout_auto_cancel"
    assert audit["approval_id"] == "apr_campaign_1"
    assert audit["workflow_run_id"] == "run_campaign_1"
    assert audit["step_id"] == "launch_ads"
    assert audit["requested_approver_role"] == "cmo"
    assert audit["created_at"] == (NOW - timedelta(hours=5)).isoformat()
    assert audit["due_at"] == (NOW - timedelta(hours=1)).isoformat()
    assert audit["timed_out_at"] == NOW.isoformat()
    assert audit["outcome"] == "auto_cancel"
    assert audit["escalation_target"] == "cmo"
    assert audit["audit_reference"].startswith("mkt_approval_timeout_")


def test_auto_escalate_changes_owner_and_creates_audit_evidence() -> None:
    decision = evaluate_approval_timeout(
        _approval(
            approval_id="apr_budget_1",
            action="mutate_ad_budget",
            workflow_id="daily_spend_optimization",
        ),
        now=NOW,
    )

    assert decision["outcome"] == "auto_escalate"
    assert decision["workflow_state"] == "waiting_hitl"
    assert decision["escalated"] is True
    assert decision["escalation_target"] == "cmo"
    assert decision["external_writes_allowed"] is False
    assert decision["audit_evidence"]["event_type"] == "cmo_approval_timeout_auto_escalate"


def test_continue_read_only_allows_only_recommendation_continuation() -> None:
    decision = evaluate_approval_timeout(
        _approval(action="publish"),
        policy_source={
            "approval_timeout_policies": {
                "content_publish": {
                    "timeout_outcome": "continue_read_only",
                    "escalation_role": "content_lead",
                }
            }
        },
        now=NOW,
    )

    assert decision["outcome"] == "continue_read_only"
    assert decision["workflow_state"] == "degraded"
    assert decision["progress_allowed"] is True
    assert decision["read_only_continuation_allowed"] is True
    assert decision["external_writes_allowed"] is False
    assert decision["next_action_cta"] == "continue_read_only"
    assert decision["audit_evidence"]["blocked_action"] == "publish"


def test_pause_workflow_pauses_and_blocks_external_writes() -> None:
    decision = evaluate_approval_timeout(
        _approval(action="send_email", workflow_id="lead_nurture"),
        now=NOW,
    )

    assert decision["outcome"] == "pause_workflow"
    assert decision["workflow_state"] == "paused"
    assert decision["external_writes_allowed"] is False
    assert decision["next_action_cta"] == "resume_after_manual_approval"
    assert decision["audit_evidence"]["event_type"] == "cmo_approval_timeout_pause_workflow"


def test_require_manual_resolution_blocks_progress_until_human_resolution() -> None:
    decision = evaluate_approval_timeout(
        _approval(action="pricing_claim", workflow_id="content_pipeline"),
        now=NOW,
    )

    assert decision["outcome"] == "require_manual_resolution"
    assert decision["workflow_state"] == "blocked"
    assert decision["progress_allowed"] is False
    assert decision["external_writes_allowed"] is False
    assert decision["audit_evidence"]["event_type"] == "cmo_approval_timeout_manual_resolution"


def test_missing_timeout_policy_blocks_production_approval_sensitive_workflow_step() -> None:
    step = {
        "id": "approve_custom_plan",
        "type": "agent",
        "agent_type": "campaign_pilot",
        "action": "create_plan",
        "approval_required": True,
    }

    result = lint_marketing_workflow(_workflow(step))

    assert result.has_errors is True
    assert "marketing_approval_timeout_policy_missing" in {finding.code for finding in result.findings}


def test_workflow_linter_accepts_default_policy_for_known_sensitive_step() -> None:
    step = {
        "id": "launch_ads",
        "type": "agent",
        "agent_type": "campaign_pilot",
        "action": "activate_campaign",
        "connector_key": "google_ads",
        "external_write_confirmation_required": True,
        "expected_confirmation_fields": ["external_object_id", "confirmed_at"],
        "idempotency_key_template": "campaign:{campaign_id}:activate",
    }

    result = lint_marketing_workflow(_workflow(step), connector_contracts=[_contract()])

    assert "marketing_approval_timeout_policy_missing" not in {finding.code for finding in result.findings}


def test_timeout_policy_does_not_activate_unrelated_workflows() -> None:
    campaign = build_workflow_approval_timeout_status(
        "campaign_launch",
        ["launch_campaign"],
        {"approval_timeout_policy_disabled": True},
    )
    weekly_report = build_workflow_approval_timeout_status(
        "weekly_marketing_report",
        [],
        {"approval_timeout_policy_disabled": True},
    )

    assert campaign["status"] == "missing_policy"
    assert campaign["next_action_cta"] == "configure_approval_timeout_policy"
    assert weekly_report["status"] == "not_required"
    assert weekly_report["next_action_cta"] == "none"


def test_approval_timeout_risk_projection_summarizes_pending_and_overdue_items() -> None:
    risk = build_approval_timeout_risk(
        [
            _approval(approval_id="pending", due_at=NOW + timedelta(hours=1)),
            _approval(approval_id="overdue", due_at=NOW - timedelta(minutes=1)),
        ],
        now=NOW,
    )

    assert risk["status"] == "blocked"
    assert risk["pending"] == 1
    assert risk["overdue"] == 1
    assert risk["blocked_external_writes"] == 1
    assert risk["next_action_cta"] == "resolve_overdue_approvals"


def test_active_external_write_after_timeout_fails_closed() -> None:
    timeout_decision = evaluate_approval_timeout(_approval(), now=NOW)

    result = evaluate_marketing_external_write_result(
        [_contract()],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        output=_confirmed_output(timeout_decision),
        step={"id": "launch_ads", "idempotency_key": "ads-launch-1"},
        state={"workflow_id": "campaign_launch", "id": "run_campaign_1"},
        now=NOW,
    )

    assert result["step_status"] == "failed"
    assert result["error_code"] == "external_write_approval_timeout"
    assert result["can_mark_complete"] is False
    assert "Approval timed out" in result["reason"]


def test_preapproved_timeout_policy_can_allow_confirmed_external_write() -> None:
    timeout_decision = evaluate_approval_timeout(
        _approval(action="publish", preapproved_after_timeout=True),
        policy_source={
            "approval_timeout_policies": {
                "content_publish": {
                    "timeout_outcome": "auto_escalate",
                    "external_writes_allowed_after_timeout": True,
                    "preapproved_after_timeout": True,
                }
            }
        },
        now=NOW,
    )

    result = evaluate_marketing_external_write_result(
        [_contract()],
        connector_key="google_ads",
        action="publish",
        workflow_mode="active",
        output=_confirmed_output(timeout_decision),
        step={"id": "publish_content", "idempotency_key": "ads-launch-1"},
        state={"workflow_id": "content_pipeline", "id": "run_content_1"},
        now=NOW,
    )

    assert timeout_decision["external_writes_allowed"] is True
    assert result["step_status"] == "completed"
    assert result["final_state"] == "write_confirmed"
