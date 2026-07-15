from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import api.v1.kpis as kpis_api
from core.marketing.approval_review import (
    build_cmo_approval_decision_request,
    build_cmo_approval_review_projection,
)
from core.marketing.approval_timeouts import build_approval_timeout_risk

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def _ready_connector(key: str = "google_ads") -> dict:
    return {
        "connector_key": key,
        "category": "Ads",
        "write_ready": True,
        "write_safe": True,
        "write_status": "ready",
        "read_status": "ready",
        "contract_state": "healthy",
        "missing_write_scopes": [],
        "mock_or_test_double": False,
        "blocks_external_writes": False,
    }


def _unsafe_connector(key: str = "google_ads") -> dict:
    return {
        **_ready_connector(key),
        "write_ready": False,
        "write_safe": False,
        "write_status": "blocked",
        "missing_write_scopes": ["adwords.write"],
        "blocks_external_writes": True,
    }


def _approval(**overrides: object) -> dict:
    base = {
        "approval_id": "apr-campaign-1",
        "workflow_id": "campaign_launch",
        "workflow_run_id": "run-1",
        "step_id": "launch",
        "action": "launch_campaign",
        "approval_type": "ad_campaign_launch",
        "status": "pending",
        "agent_id": "agent-campaign",
        "agent_type": "campaign_pilot",
        "requested_approver": "cmo@example.com",
        "requested_approver_role": "cmo",
        "created_at": (NOW - timedelta(hours=1)).isoformat(),
        "due_at": (NOW + timedelta(hours=3)).isoformat(),
        "preview_payload": {
            "campaign_name": "Q2 Pipeline Sprint",
            "channels": ["google_ads"],
            "headline": "Pipeline without guesswork",
        },
        "before_after_diff": {
            "before": "Campaign is draft only",
            "after": "Campaign launches to search audience",
        },
        "budget_impact": {"amount": 1200, "currency": "USD", "period": "daily"},
        "audience_impact": {"estimated_recipients": 25000, "segments": ["enterprise_search"]},
        "brand_legal_risk_flags": ["budget_change"],
        "source_refs": [{"type": "campaign_brief", "id": "brief-1"}],
        "connector_key": "google_ads",
        "agent_rationale": "ROAS shadow runs exceeded launch threshold.",
        "audit_refs": ["audit-approval-context"],
        "rollback_stop_plan": {
            "summary": "Pause campaign and restore previous budget cap.",
        },
        "external_write_required": True,
        "customer_facing": True,
        "workflow_mode": "active",
    }
    base.update(overrides)
    return base


def _projection(approval: dict, connectors: list[dict] | None = None) -> dict:
    return build_cmo_approval_review_projection(
        [approval],
        connector_contracts=connectors or [_ready_connector()],
        approval_timeout_risk=build_approval_timeout_risk([approval], now=NOW),
        now=NOW,
    )


def _review(approval: dict, connectors: list[dict] | None = None) -> dict:
    return _projection(approval, connectors)["cmo_approval_reviews"][0]


def test_approval_review_includes_preview_diff_rationale_and_source_refs() -> None:
    review = _review(_approval())

    assert review["preview_payload"]["campaign_name"] == "Q2 Pipeline Sprint"
    assert review["before_after_diff"]["after"] == "Campaign launches to search audience"
    assert review["agent_rationale"] == "ROAS shadow runs exceeded launch threshold."
    assert review["source_refs"] == [{"type": "campaign_brief", "id": "brief-1"}]
    assert review["policy_result"]["decision"] == "requires_approval"
    assert review["timeout_state"] == "pending"
    assert "approve" in review["allowed_reviewer_actions"]


def test_campaign_launch_approval_includes_budget_audience_and_rollback_plan() -> None:
    review = _review(_approval())

    assert review["action_type"] == "campaign_launch"
    assert review["budget_impact"]["amount"] == 1200
    assert review["audience_impact"]["estimated_recipients"] == 25000
    assert "Pause campaign" in review["rollback_stop_plan"]["summary"]


def test_content_email_approval_includes_brand_legal_risk_flags() -> None:
    review = _review(
        _approval(
            approval_id="apr-email-1",
            action="send_email",
            approval_type="email_send",
            connector_key="mailchimp",
            brand_legal_risk_flags=["legal_claim", "unsubscribe_compliance"],
        ),
        [_ready_connector("mailchimp")],
    )

    assert review["action_type"] == "email_send"
    assert "legal_claim" in review["risk_flags"]
    assert "unsubscribe_compliance" in review["risk_flags"]


def test_crisis_response_approval_includes_escalation_timeout_and_policy_refs() -> None:
    review = _review(
        _approval(
            approval_id="apr-crisis-1",
            action="public_response",
            approval_type="crisis_public_response",
            connector_key="buffer",
            crisis_response=True,
            public_response=True,
            brand_legal_risk_flags=["crisis"],
        ),
        [_ready_connector("buffer")],
    )

    assert review["action_type"] == "crisis_public_response"
    assert review["policy_result"]["decision"] == "requires_escalation"
    assert review["policy_result_ref"]
    assert review["escalation_result_ref"]
    assert review["timeout_result_ref"]
    assert "approve" not in review["allowed_reviewer_actions"]
    assert "escalate" in review["allowed_reviewer_actions"]


def test_approval_fails_closed_when_policy_result_is_missing() -> None:
    review = _review(_approval(marketing_policy_manifest_disabled=True))

    assert review["status"] == "blocked"
    assert review["policy_result"]["decision"] == "missing_policy"
    assert "approve" not in review["allowed_reviewer_actions"]
    assert any("policy" in reason.lower() for reason in review["blocked_reasons"])


def test_approval_fails_closed_when_connector_write_readiness_is_unsafe() -> None:
    review = _review(_approval(), [_unsafe_connector()])

    assert review["status"] == "blocked"
    assert review["external_write_readiness"]["status"] == "unsafe"
    assert "approve" not in review["allowed_reviewer_actions"]
    assert "adwords.write" in review["external_write_readiness"]["reason"]


def test_approval_fails_closed_when_timeout_requires_manual_resolution() -> None:
    approval = _approval(
        approval_id="apr-landing-1",
        action="update_landing_page",
        approval_type="landing_page_change",
        connector_key="wordpress",
        due_at=(NOW - timedelta(minutes=1)).isoformat(),
    )
    review = _review(approval, [_ready_connector("wordpress")])

    assert review["status"] == "timed_out"
    assert review["timeout_result"]["outcome"] == "require_manual_resolution"
    assert "approve" not in review["allowed_reviewer_actions"]
    assert any("manual resolution" in reason for reason in review["blocked_reasons"])


def test_approval_fails_closed_when_required_audit_evidence_is_missing() -> None:
    approval = _approval()
    approval.pop("audit_refs")
    review = _review(approval)

    assert review["status"] == "blocked"
    assert review["audit_evidence"]["ready"] is False
    assert "approve" not in review["allowed_reviewer_actions"]


def test_reject_and_override_decision_requests_record_reason_and_replacement() -> None:
    review = _review(_approval())

    rejected = build_cmo_approval_decision_request(
        review,
        decision="reject",
        actor_id="user-1",
        actor_role="cmo",
        reason="Audience is too broad.",
        now=NOW,
    )
    assert rejected["decision"] == "reject"
    assert rejected["reason"] == "Audience is too broad."
    assert rejected["decision_audit_ref"]

    override = build_cmo_approval_decision_request(
        review,
        decision="override",
        actor_id="user-1",
        actor_role="cmo",
        reason="Reduce budget and narrow the segment.",
        replacement_action={"action_id": "launch-v2", "budget_amount": 600},
        now=NOW,
    )
    assert override["decision"] == "override"
    assert override["replacement_action"]["budget_amount"] == 600
    assert override["decision_audit"]["override"]["reason"] == "Reduce budget and narrow the segment."


def test_allowed_reviewer_actions_change_based_on_status_and_risk() -> None:
    ready = _review(_approval())
    blocked = _review(_approval(marketing_policy_manifest_disabled=True))
    timed_out = _review(
        _approval(
            action="update_landing_page",
            approval_type="landing_page_change",
            connector_key="wordpress",
            due_at=(NOW - timedelta(minutes=1)).isoformat(),
        ),
        [_ready_connector("wordpress")],
    )

    assert "approve" in ready["allowed_reviewer_actions"]
    assert "approve" not in blocked["allowed_reviewer_actions"]
    assert "request_changes" in blocked["allowed_reviewer_actions"]
    assert "approve" not in timed_out["allowed_reviewer_actions"]
    assert "pause" in timed_out["allowed_reviewer_actions"]


def test_work_queue_link_is_present_on_approval_review_payload() -> None:
    review = _review(_approval(approval_id="apr-link-1"))

    assert review["related_work_queue_item_ids"][0].startswith("cmo_wq_")


@pytest.mark.asyncio
async def test_kpis_cmo_exposes_approval_review_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_base(tenant_id: str, role: str, company_id: str) -> dict:
        return {
            "demo": False,
            "company_id": company_id,
            "agent_count": 1,
            "total_tasks_30d": 1,
            "success_rate": 100,
            "hitl_interventions": 1,
            "total_cost_usd": 0,
            "domain_breakdown": [],
        }

    async def fake_configs(tenant_id: str, company_id: str | None = None) -> list:
        return []

    async def fake_approval_timeout(tenant_id: str, company_id: str) -> dict:
        approval = _approval(external_write_required=False, customer_facing=False)
        risk = build_approval_timeout_risk([approval], now=NOW)
        risk["approval_records"] = [approval]
        return risk

    monkeypatch.setattr(kpis_api, "_build_kpi_response", fake_base)
    monkeypatch.setattr(kpis_api, "_load_marketing_connector_configs", fake_configs)
    monkeypatch.setattr(kpis_api, "_load_cmo_approval_timeout_risk", fake_approval_timeout)

    response = await kpis_api._build_cmo_kpi_response("tenant-1", "company-1")

    assert "cmo_approval_reviews" in response
    assert response["cmo_approval_review_summary"]["total"] == 1
    assert response["approval_timeout_risk"]["approval_timeout_decisions"][0]["approval_review_id"]
