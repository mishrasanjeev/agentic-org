from __future__ import annotations

from typing import Any

import pytest

from core.agents.marketing.social_media import SocialMediaAgent
from core.agents.registry import AgentRegistry
from core.marketing.agent_contracts import (
    build_marketing_agent_contract_output,
    contract_has_required_shape,
    get_marketing_agent_contract_spec,
)
from core.marketing.pilot_proof import build_cmo_pilot_proof_projection
from core.marketing.workflow_linter import lint_marketing_workflow
from core.schemas.messages import TargetAgent, TaskAssignment, TaskInput


class FakeToolGateway:
    def __init__(self, responses: dict[tuple[str, str], dict[str, Any] | Exception]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        agent_scopes: list[str],
        connector_name: str,
        tool_name: str,
        params: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "agent_scopes": agent_scopes,
                "connector_name": connector_name,
                "tool_name": tool_name,
                "params": params,
                "idempotency_key": idempotency_key,
            }
        )
        response = self.responses.get((connector_name, tool_name), {})
        if isinstance(response, Exception):
            raise response
        return dict(response)


def _assignment(action: str, inputs: dict[str, Any]) -> TaskAssignment:
    return TaskAssignment(
        message_id=f"msg-social-{action}",
        correlation_id="corr-social",
        workflow_run_id="run-social",
        workflow_definition_id="wf-social",
        step_id=f"step-{action}",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(
            agent_id="agent-social",
            agent_type="social_media",
            agent_token="test-token",
        ),
        task=TaskInput(action=action, inputs=inputs),
    )


def _agent(gateway: FakeToolGateway | None = None) -> SocialMediaAgent:
    return SocialMediaAgent(
        agent_id="agent-social",
        tenant_id="tenant-cmo",
        tool_gateway=gateway,
        authorized_tools=["buffer.create_post", "twitter.create_tweet", "youtube.create_community_post"],
    )


def _workflow_step(action: str, **overrides: object) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "step_social",
        "name": "Social Step",
        "type": "agent",
        "agent_type": "social_media",
        "action": action,
    }
    step.update(overrides)
    return step


def _workflow(step: dict[str, Any], *, mode: str = "production") -> dict[str, Any]:
    return {
        "id": "wf_social",
        "name": "Social Publishing",
        "domain": "marketing",
        "mode": mode,
        "steps": [step],
    }


def _write_contract(**overrides: object) -> dict[str, Any]:
    contract = {
        "connector_key": "buffer",
        "category": "Social",
        "read_ready": True,
        "write_ready": True,
        "write_safe": True,
        "idempotency_key_supported": True,
        "retry_budget": {
            "idempotency_key": "social:post:001",
            "idempotency_supported": True,
            "remaining_attempts": 2,
        },
    }
    contract.update(overrides)
    return contract


@pytest.mark.asyncio
async def test_social_media_content_calendar_generation_has_contract_shape() -> None:
    result = await _agent().execute(
        _assignment(
            "generate_content_calendar",
            {
                "campaign_theme": "pilot proof launch",
                "channels": ["linkedin", "twitter"],
                "weeks": 2,
                "start_date": "2026-05-25",
                "target_audience": "CMOs",
            },
        )
    )
    contract = build_marketing_agent_contract_output(
        "social_media",
        "generate_content_calendar",
        result=result,
        audit_ref=result.output["audit_ref"],
    )

    assert result.status == "completed"
    assert result.output["status"] == "calendar_generated"
    assert len(result.output["content_calendar"]) == 4
    assert result.output["approval_required"] is False
    assert contract_has_required_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False


@pytest.mark.asyncio
async def test_posting_schedule_optimization_is_deterministic() -> None:
    result = await _agent().execute(
        _assignment(
            "optimize_posting_schedule",
            {
                "channels": ["linkedin", "twitter"],
                "historical_engagement": [
                    {"channel": "linkedin", "day": "Mon", "hour": "09:00", "engagement_rate": 0.02},
                    {"channel": "linkedin", "day": "Tue", "hour": "10:00", "engagement_rate": 0.07},
                    {"channel": "twitter", "day": "Wed", "hour": "11:00", "engagement_rate": 0.04},
                ],
            },
        )
    )

    slots = {row["channel"]: row["recommended_slot"] for row in result.output["schedule_recommendations"]}
    assert result.status == "completed"
    assert slots["linkedin"] == "Tue 10:00"
    assert slots["twitter"] == "Wed 11:00"


@pytest.mark.asyncio
async def test_engagement_triage_classifies_normal_negative_legal_executive_and_crisis() -> None:
    result = await _agent().execute(
        _assignment(
            "triage_engagement",
            {
                "mentions": [
                    {"id": "m1", "text": "Great product update"},
                    {"id": "m2", "text": "I am angry about broken support"},
                    {"id": "m3", "text": "Do you guarantee pricing vs competitor?"},
                    {"id": "m4", "text": "Can your CEO comment to the press?"},
                    {"id": "m5", "text": "Data breach and fraud allegations"},
                ]
            },
        )
    )

    by_id = {row["mention_id"]: row for row in result.output["mentions"]}
    assert by_id["m1"]["risk_type"] == "normal"
    assert by_id["m2"]["risk_type"] == "negative_feedback"
    assert by_id["m3"]["severity"] == "high"
    assert by_id["m4"]["risk_type"] == "executive_or_press"
    assert by_id["m5"]["risk_type"] == "crisis"
    assert result.status == "hitl_triggered"
    assert result.output["approval_required"] is True
    assert result.output["escalation_ref"]


@pytest.mark.asyncio
async def test_high_risk_reply_requires_policy_review_and_escalation_ref() -> None:
    result = await _agent().execute(
        _assignment(
            "classify_reply_risk",
            {"reply_text": "We guarantee lower pricing than every competitor."},
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["classification"]["severity"] == "high"
    assert result.output["policy_result"]["decision"] in {"requires_approval", "requires_escalation"}
    assert result.output["approval_required"] is True
    assert result.output["audit_ref"].startswith("social_media:")


@pytest.mark.asyncio
async def test_shadow_mode_cannot_publish_or_post_externally() -> None:
    gateway = FakeToolGateway({("buffer", "create_post"): {"status": "write_confirmed"}})
    result = await _agent(gateway).execute(
        _assignment(
            "schedule_post",
            {
                "workflow_mode": "shadow",
                "post_text": "Launch update for customers.",
                "channel": "linkedin",
                "connector_write_ready": True,
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "shadow_only"
    assert result.output["external_writes_completed"] is False
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_active_publish_blocks_when_connector_not_write_safe() -> None:
    result = await _agent().execute(
        _assignment(
            "schedule_post",
            {
                "workflow_mode": "active",
                "post_text": "Launch update for customers.",
                "channel": "linkedin",
                "connector_contract": _write_contract(write_ready=False, write_safe=False),
                "approved": True,
                "approval_ref": "approval-social-1",
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "blocked"
    assert any("not write-safe" in reason for reason in result.output["blocked_reasons"])


@pytest.mark.asyncio
async def test_active_publish_cannot_complete_without_confirmed_external_write() -> None:
    gateway = FakeToolGateway(
        {("buffer", "create_post"): {"status": "accepted", "external_object_id": "buf-accepted"}}
    )
    result = await _agent(gateway).execute(
        _assignment(
            "schedule_post",
            {
                "workflow_mode": "active",
                "post_text": "Launch update for customers.",
                "channel": "linkedin",
                "connector_contract": _write_contract(),
                "approved": True,
                "approval_ref": "approval-social-1",
                "idempotency_key": "social:post:launch",
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["external_write_confirmation_status"] == "accepted"
    assert result.output["external_writes_completed"] is False
    assert any("unconfirmed" in reason for reason in result.output["blocked_reasons"])


@pytest.mark.asyncio
async def test_confirmed_active_publish_has_policy_audit_and_write_ref() -> None:
    gateway = FakeToolGateway(
        {
            ("buffer", "create_post"): {
                "status": "write_confirmed",
                "connector_key": "buffer",
                "external_object_id": "buf-post-1",
                "source_url": "https://social.example/post/1",
                "idempotency_key": "social:post:launch",
                "confirmed_at": "2026-05-24T12:00:00+00:00",
                "audit_ref": "audit-social-write-1",
            }
        }
    )
    result = await _agent(gateway).execute(
        _assignment(
            "schedule_post",
            {
                "workflow_mode": "active",
                "post_text": "Launch update for customers.",
                "channel": "linkedin",
                "connector_contract": _write_contract(),
                "approved": True,
                "approval_ref": "approval-social-1",
                "idempotency_key": "social:post:launch",
            },
        )
    )

    assert result.status == "completed"
    assert result.output["external_write_confirmation_status"] == "write_confirmed"
    assert result.output["external_writes_completed"] is True
    assert result.output["external_write_ref"]["external_object_id"] == "buf-post-1"
    assert result.output["policy_result"]["decision"] == "requires_approval"
    assert result.output["audit_ref"].startswith("social_media:")


def test_social_media_registered_and_contract_metadata_is_beta() -> None:
    assert AgentRegistry.get_by_type("social_media") is SocialMediaAgent
    spec = get_marketing_agent_contract_spec("social_media")
    assert spec["maturity"] == "beta"
    assert spec["production_ready"] is False
    assert spec["surface"] == "core.agents.marketing.social_media"
    assert "generate_content_calendar" in spec["actions"]


def test_workflow_linter_knows_social_media_but_blocks_unsafe_production_write() -> None:
    recommendation = lint_marketing_workflow(
        _workflow(_workflow_step("generate_content_calendar"))
    )
    unsafe_write = lint_marketing_workflow(
        _workflow(_workflow_step("schedule_post", connector_key="buffer")),
        connector_contracts=[_write_contract()],
    )

    assert "marketing_agent_type_unknown" not in {finding.code for finding in recommendation.findings}
    assert "marketing_agent_unavailable_for_production" not in {
        finding.code for finding in recommendation.findings
    }
    assert unsafe_write.has_errors is True
    assert "marketing_external_write_confirmation_metadata_missing" in {
        finding.code for finding in unsafe_write.findings
    }
    assert "marketing_decision_audit_evidence_missing" in {
        finding.code for finding in unsafe_write.findings
    }


def test_social_media_production_lint_passes_only_with_write_policy_audit_metadata() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _workflow_step(
                "schedule_post",
                connector_key="buffer",
                external_write_confirmation_required=True,
                expected_confirmation_fields=["external_object_id", "confirmed_at"],
                idempotency_key_template="social:{post_id}:schedule",
                decision_audit_required=True,
                approval_timeout_policy={"approval_type": "social_post_target_behavior"},
            )
        ),
        connector_contracts=[_write_contract()],
    )

    codes = {finding.code for finding in result.findings}
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_external_write_connector_not_ready" not in codes
    assert "marketing_external_write_confirmation_metadata_missing" not in codes


def test_pilot_proof_keeps_social_media_unproven_without_production_proof() -> None:
    projection = build_cmo_pilot_proof_projection(
        environment_type="vendor_sandbox",
        source_context={"tenant_id": "tenant-cmo", "source": "vendor_sandbox"},
        connector_setup=[],
        connector_contracts=[],
        agent_contracts=[get_marketing_agent_contract_spec("social_media")],
    )
    proof = projection["cmo_pilot_proof"]
    proven = {row["capability_key"] for row in proof["proven_capabilities"]}
    unproven = {row["capability_key"]: row for row in proof["unproven_capabilities"]}

    assert "agent:social_media" not in proven
    assert unproven["agent:social_media"]["status"] == "beta"
    assert proof["production_claim_allowed"] is False
