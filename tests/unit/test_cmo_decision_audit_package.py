from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.marketing.approval_timeouts import evaluate_approval_timeout
from core.marketing.decision_audit import (
    build_cmo_decision_audit_package,
    build_workflow_decision_audit_status,
    build_workflow_promotion_audit_package,
    serialize_cmo_decision_audit_package,
)
from core.marketing.escalation_matrix import evaluate_marketing_escalation
from core.marketing.external_writes import evaluate_marketing_external_write_result
from core.marketing.policy_manifest import evaluate_marketing_policy
from core.marketing.workflow_linter import lint_marketing_workflow

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def test_audit_package_has_stable_id_shape_and_worm_serialization() -> None:
    context = {
        "tenant_id": "tenant-1",
        "company_id": "company-1",
        "workflow_id": "campaign_launch",
        "workflow_run_id": "run-1",
        "step_id": "launch",
        "agent": "campaign_pilot",
        "action": "launch_campaign",
        "actor_type": "agent",
        "actor_id": "agent-campaign-pilot",
        "input_snapshot": {
            "campaign": "Spring launch",
            "api_token": "secret-token-value",
        },
        "source_refs": [{"type": "brief", "id": "brief-1"}],
        "rationale": "Launch meets policy and connector prerequisites.",
        "alternatives_considered": ["keep_draft", "reduce_budget"],
        "risk_flags": ["budget"],
        "confidence": 0.91,
        "final_outcome": "requires_approval",
        "created_at": NOW.isoformat(),
    }

    first = build_cmo_decision_audit_package(context, now=NOW)
    second = build_cmo_decision_audit_package(context, now=NOW)
    serialized = serialize_cmo_decision_audit_package(first)

    assert first["audit_id"] == second["audit_id"]
    assert first["schema_version"] == "2026-05-23.cmo-6.3"
    assert first["event_type"] == "campaign_launch"
    assert first["input_snapshot_hash"]
    assert first["source_refs"] == [{"type": "brief", "id": "brief-1"}]
    assert first["worm_ready"] is True
    assert first["serialization"]["format"] == "canonical_json"
    assert "secret-token-value" not in serialized
    assert "[REDACTED]" in serialized


def test_policy_decision_creates_audit_ref_and_evidence() -> None:
    decision = evaluate_marketing_policy(
        {
            "workflow_id": "campaign_launch",
            "workflow_mode": "active",
            "action": "launch_campaign",
            "external_write_required": True,
        }
    )

    package = decision["decision_audit"]
    assert decision["audit_reference"] == package["audit_reference"]
    assert package["event_type"] == "policy_decision"
    assert package["policy_result"]["decision"] == "requires_approval"
    assert package["policy_result_ref"].startswith("policy:")


def test_escalation_decision_creates_audit_ref_and_evidence() -> None:
    decision = evaluate_marketing_escalation(
        {
            "trigger_type": "crisis_public_response",
            "workflow_id": "brand_crisis_response",
            "step_id": "public_response",
        },
        now=NOW,
    )

    assert decision["decision_audit"]["event_type"] == "escalation_decision"
    assert decision["decision_audit_ref"].startswith("cmo_decision_audit:")
    assert decision["evidence"]["decision_audit_ref"] == decision["decision_audit_ref"]


def test_approval_timeout_creates_audit_ref_and_evidence() -> None:
    decision = evaluate_approval_timeout(
        {
            "approval_id": "approval-1",
            "workflow_id": "campaign_launch",
            "workflow_run_id": "run-1",
            "step_id": "launch",
            "action": "launch_campaign",
            "created_at": NOW - timedelta(hours=5),
            "due_at": NOW - timedelta(hours=1),
            "status": "pending",
        },
        now=NOW,
    )

    assert decision["timed_out"] is True
    assert decision["decision_audit"]["event_type"] == "approval_timeout"
    assert decision["audit_evidence"]["decision_audit_ref"] == decision["decision_audit_ref"]
    assert decision["decision_audit"]["timeout_result_ref"].startswith("mkt_approval_timeout")


def test_external_write_attempt_and_confirmation_create_audit_evidence() -> None:
    decision = evaluate_marketing_external_write_result(
        [_write_contract()],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        output={
            "external_write_state": "accepted",
            "external_object_id": "customers/123/campaigns/456",
            "source_url": "https://ads.google.com/campaigns/456",
            "idempotency_key": "launch-1",
            "request_fingerprint": "fp-launch-1",
        },
        step={"id": "launch", "idempotency_key": "launch-1"},
        state={
            "tenant_id": "tenant-1",
            "workflow_id": "campaign_launch",
            "workflow_run_id": "run-1",
            "marketing_policy_approval_satisfied": True,
        },
        now=NOW,
    )

    attempt, final = decision["audit_events"]
    assert attempt["decision_audit"]["event_type"] == "external_write_attempt"
    assert final["decision_audit"]["event_type"] == "external_write_confirmation"
    assert final["decision_audit"]["external_write_result"]["external_object_id"].endswith("/456")
    assert decision["decision_audit_ref"] == final["decision_audit_ref"]


def test_external_write_rejection_timeout_and_recovery_create_final_audit_evidence() -> None:
    rejected = evaluate_marketing_external_write_result(
        [_write_contract()],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        output={
            "external_write_state": "rejected",
            "idempotency_key": "launch-1",
            "rejection_reason": "Vendor rejected budget payload.",
        },
        step={"id": "launch", "idempotency_key": "launch-1"},
        state={"workflow_id": "campaign_launch", "marketing_policy_approval_satisfied": True},
        now=NOW,
    )
    timeout = evaluate_marketing_external_write_result(
        [_write_contract(idempotency_key_supported=False)],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        output={
            "external_write_state": "timeout_unknown",
            "request_fingerprint": "fp-timeout",
        },
        step={"id": "launch"},
        state={"workflow_id": "campaign_launch", "marketing_policy_approval_satisfied": True},
        now=NOW,
    )
    recovered = evaluate_marketing_external_write_result(
        [
            _write_contract(
                confirmations=[
                    {
                        "action": "launch_campaign",
                        "status": "write_confirmed",
                        "idempotency_key": "launch-1",
                        "external_object_id": "customers/123/campaigns/456",
                        "confirmed_at": NOW.isoformat(),
                    }
                ]
            )
        ],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        output={
            "external_write_state": "timeout_unknown",
            "idempotency_key": "launch-1",
            "request_fingerprint": "fp-recovered",
        },
        step={"id": "launch", "idempotency_key": "launch-1"},
        state={"workflow_id": "campaign_launch", "marketing_policy_approval_satisfied": True},
        now=NOW,
    )

    assert rejected["audit_events"][-1]["decision_audit"]["event_type"] == "external_write_rejection"
    assert timeout["audit_events"][-1]["decision_audit"]["event_type"] == "external_write_timeout"
    assert recovered["audit_events"][-1]["decision_audit"]["event_type"] == "external_write_idempotent_recovery"


def test_workflow_promotion_and_override_are_auditable() -> None:
    promotion = build_workflow_promotion_audit_package(
        {
            "workflow_key": "campaign_launch",
            "state": "active",
            "marketing_policy": {"status": "ready"},
            "escalation_matrix": {"status": "ready"},
            "approval_timeout_policy": {"status": "ready"},
        },
        now=NOW,
    )
    override = build_cmo_decision_audit_package(
        {
            "event_type": "override",
            "workflow_id": "campaign_launch",
            "action": "launch_campaign",
            "actor_type": "user",
            "actor_id": "user-1",
            "actor_role": "cmo",
            "override_reason": "Replace launch with lower-risk draft.",
            "replacement_action": "create_draft",
            "created_at": NOW.isoformat(),
        },
        now=NOW,
    )

    assert promotion["event_type"] == "workflow_promotion"
    assert promotion["final_outcome"] == "active"
    assert override["override"]["actor_id"] == "user-1"
    assert override["override"]["reason"] == "Replace launch with lower-risk draft."
    assert override["override"]["replacement_action"] == "create_draft"


def test_missing_audit_evidence_blocks_production_customer_facing_lint() -> None:
    result = lint_marketing_workflow(
        {
            "id": "wf_campaign",
            "domain": "marketing",
            "mode": "production",
            "steps": [
                {
                    "id": "launch",
                    "type": "agent",
                    "agent_type": "campaign_pilot",
                    "action": "launch_campaign",
                    "connector_key": "google_ads",
                    "external_write_confirmation_required": True,
                    "expected_confirmation_fields": ["external_object_id"],
                    "idempotency_key": "launch-1",
                }
            ],
        },
        connector_contracts=[_write_contract()],
    )

    assert result.has_errors is True
    assert "marketing_decision_audit_evidence_missing" in {
        finding.code for finding in result.errors
    }


def test_shadow_read_only_decision_is_auditable_and_non_executable() -> None:
    package = build_cmo_decision_audit_package(
        {
            "event_type": "shadow_only",
            "workflow_id": "weekly_marketing_report",
            "action": "recommend",
            "actor_type": "agent",
            "rationale": "Recommendation only; no external execution.",
            "final_outcome": "shadow_only",
            "created_at": NOW.isoformat(),
        },
        now=NOW,
    )

    assert package["event_type"] == "shadow_only"
    assert package["final_outcome"] == "shadow_only"
    assert package["external_write_result"] is None


def test_workflow_audit_status_blocks_when_decision_audit_disabled() -> None:
    status = build_workflow_decision_audit_status(
        "campaign_launch",
        ["launch_campaign"],
        {"decision_audit_disabled": True},
    )

    assert status["status"] == "missing_audit_evidence"
    assert status["next_action_cta"] == "configure_decision_audit_package"


def _write_contract(
    *,
    idempotency_key_supported: bool = True,
    confirmations: list[dict] | None = None,
) -> dict:
    return {
        "connector_key": "google_ads",
        "category": "Ads",
        "read_ready": True,
        "write_ready": True,
        "write_safe": True,
        "idempotency_key_supported": idempotency_key_supported,
        "retry_budget": {
            "idempotency_key": "launch-1",
            "idempotency_supported": idempotency_key_supported,
            "remaining_attempts": 2,
            "next_retry_at": "2026-05-23T12:05:00+00:00",
        },
        "external_write_confirmations": confirmations or [],
    }
