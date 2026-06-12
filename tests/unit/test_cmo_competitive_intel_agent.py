from __future__ import annotations

from typing import Any

import pytest

from core.agents.marketing.competitive_intel import CompetitiveIntelAgent
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
        message_id=f"msg-competitive-{action}",
        correlation_id="corr-competitive",
        workflow_run_id="run-competitive",
        workflow_definition_id="wf-competitive",
        step_id=f"step-{action}",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(
            agent_id="agent-competitive",
            agent_type="competitive_intel",
            agent_token="test-token",
        ),
        task=TaskInput(action=action, inputs=inputs),
    )


def _agent(gateway: FakeToolGateway | None = None) -> CompetitiveIntelAgent:
    return CompetitiveIntelAgent(
        agent_id="agent-competitive",
        tenant_id="tenant-cmo",
        tool_gateway=gateway,
        authorized_tools=["buffer.publish_competitive_response", "linkedin_ads.launch_competitive_campaign"],
    )


def _profiles() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    previous = [
        {
            "name": "Acme CRM",
            "domain": "acme.example",
            "segment": "enterprise",
            "price": 100,
            "currency": "USD",
            "features": ["sso", "analytics"],
            "source": "brandwatch",
            "source_url": "https://intel.example/acme-old",
            "observed_at": "2026-05-10",
        }
    ]
    current = [
        {
            "name": "Acme CRM",
            "domain": "acme.example",
            "segment": "enterprise",
            "price": 125,
            "currency": "USD",
            "features": ["sso", "analytics", "ai assistant", "governance"],
            "source": "brandwatch",
            "source_url": "https://intel.example/acme-new",
            "observed_at": "2026-05-24",
        }
    ]
    return previous, current


def _base_inputs(**overrides: Any) -> dict[str, Any]:
    previous, current = _profiles()
    payload: dict[str, Any] = {
        "previous_profiles": previous,
        "current_profiles": current,
        "win_loss_notes": [
            {
                "deal_id": "deal-1",
                "competitor": "Acme CRM",
                "text": "Closed lost to Acme due to pricing and missing governance feature.",
                "created_at": "2026-05-23",
            }
        ],
        "source_refs": [{"type": "brandwatch", "ref_id": "query-1"}],
        "workflow_mode": "shadow",
    }
    payload.update(overrides)
    return payload


def _write_contract(**overrides: object) -> dict[str, Any]:
    contract = {
        "connector_key": "buffer",
        "category": "Social",
        "read_ready": True,
        "write_ready": True,
        "write_safe": True,
        "idempotency_key_supported": True,
        "retry_budget": {
            "idempotency_key": "competitive:write:001",
            "idempotency_supported": True,
            "remaining_attempts": 2,
        },
    }
    contract.update(overrides)
    return contract


def _workflow(step: dict[str, Any], *, mode: str = "production") -> dict[str, Any]:
    return {
        "id": "wf_competitive",
        "name": "Competitive Intel Monitoring",
        "domain": "marketing",
        "mode": mode,
        "steps": [step],
    }


def _workflow_step(action: str, **overrides: object) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "step_competitive",
        "name": "Competitive Step",
        "type": "agent",
        "agent_type": "competitive_intel",
        "action": action,
    }
    step.update(overrides)
    return step


def _proof_contracts() -> list[dict[str, Any]]:
    return [
        {
            "agent_type": "campaign_pilot",
            "maturity": "production",
            "production_ready": True,
            "blocker": None,
        },
        get_marketing_agent_contract_spec("competitive_intel"),
    ]


@pytest.mark.asyncio
async def test_competitive_intel_happy_path_weekly_snapshot_has_contract_shape() -> None:
    result = await _agent().execute(_assignment("weekly_market_snapshot", _base_inputs()))
    contract = build_marketing_agent_contract_output(
        "competitive_intel",
        "weekly_market_snapshot",
        result=result,
        audit_ref=result.output["audit_ref"],
    )

    assert result.status == "completed"
    assert result.output["status"] == "snapshot_ready"
    assert result.output["competitor_snapshot"]["profiles"][0]["domain"] == "acme.example"
    assert result.output["competitor_snapshot"]["pricing_changes"]
    assert result.output["competitor_snapshot"]["feature_diffs"]
    assert result.output["competitor_snapshot"]["win_loss_signals"]
    assert contract_has_required_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False


@pytest.mark.asyncio
async def test_competitor_profile_normalization_is_deterministic() -> None:
    result = await _agent().execute(
        _assignment(
            "normalize_competitor_profiles",
            {
                "competitors": [
                    {
                        "competitor": " ACME CRM ",
                        "website": "Acme.Example",
                        "market_segment": "Enterprise",
                        "features": "SSO, Analytics, SSO",
                        "pricing": {"amount": "99", "currency": "USD", "plan": "team"},
                    }
                ]
            },
        )
    )

    profile = result.output["normalized_profiles"][0]
    assert profile["name"] == "ACME CRM"
    assert profile["domain"] == "acme.example"
    assert profile["segment"] == "enterprise"
    assert profile["pricing"]["amount"] == 99
    assert profile["features"] == ["analytics", "sso"]


@pytest.mark.asyncio
async def test_pricing_change_detection_returns_confidence_score() -> None:
    result = await _agent().execute(_assignment("pricing_change_detection", _base_inputs()))
    pricing_change = result.output["pricing_changes"][0]

    assert pricing_change["change_type"] == "pricing"
    assert pricing_change["value_delta"]["delta_pct"] == 25
    assert pricing_change["confidence"] >= 0.8


@pytest.mark.asyncio
async def test_feature_capability_diffing_detects_added_features() -> None:
    result = await _agent().execute(_assignment("feature_capability_diffing", _base_inputs()))
    diff = result.output["feature_diffs"][0]

    assert diff["change_type"] == "feature"
    assert diff["value_delta"]["added"] == ["ai assistant", "governance"]


@pytest.mark.asyncio
async def test_win_loss_signal_extraction_classifies_loss_reason() -> None:
    result = await _agent().execute(_assignment("win_loss_signal_extraction", _base_inputs()))
    signal = result.output["win_loss_signals"][0]

    assert signal["change_type"] == "win_loss_loss"
    assert signal["value_delta"]["reason"] == "pricing"


@pytest.mark.asyncio
async def test_duplicate_change_suppression_dedupes_repeated_changes() -> None:
    duplicate = {
        "competitor": "Acme CRM",
        "type": "pricing",
        "summary": "Acme lowered entry plan pricing.",
        "severity": "high",
        "confidence": 0.8,
        "observed_at": "2026-05-24",
    }
    result = await _agent().execute(
        _assignment(
            "duplicate_change_suppression",
            {"changes": [duplicate, dict(duplicate)], "workflow_mode": "shadow"},
        )
    )

    assert result.output["suppressed_duplicate_count"] == 1


@pytest.mark.asyncio
async def test_alert_threshold_severity_behavior() -> None:
    result = await _agent().execute(
        _assignment(
            "evaluate_alert_thresholds",
            {
                "changes": [
                    {
                        "competitor": "Acme CRM",
                        "type": "major_launch",
                        "summary": "Acme launched an enterprise AI suite.",
                        "severity": "critical",
                        "confidence": 0.9,
                    }
                ],
                "alert_thresholds": {"critical": 0.85},
            },
        )
    )

    alert = result.output["alerts"][0]
    assert alert["severity"] == "critical"
    assert alert["requires_escalation"] is True


@pytest.mark.asyncio
async def test_positioning_recommendation_next_best_action_output() -> None:
    result = await _agent().execute(_assignment("positioning_recommendation", _base_inputs()))
    action = result.output["positioning_recommendations"][0]

    assert action["action"] in {"prepare_battlecard_update", "prepare_positioning_update"}
    assert action["mode"] == "read_only_recommendation"


@pytest.mark.asyncio
async def test_comparative_or_pricing_claim_requires_policy_approval_and_escalation() -> None:
    result = await _agent().execute(
        _assignment(
            "comparative_claim",
            {
                "workflow_mode": "active",
                "connector_write_ready": True,
                "claim": "We are cheaper than Acme for regulated enterprises.",
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["approval_required"] is True
    assert result.output["policy_result"]["decision"] in {"requires_approval", "requires_escalation"}
    assert result.output["escalation_ref"]


@pytest.mark.asyncio
async def test_major_competitor_launch_or_crisis_signal_requires_escalation() -> None:
    result = await _agent().execute(
        _assignment(
            "weekly_market_snapshot",
            {
                "changes": [
                    {
                        "competitor": "Acme CRM",
                        "type": "major_launch",
                        "summary": "Acme launched a major crisis response campaign.",
                        "severity": "critical",
                        "confidence": 0.92,
                    }
                ]
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["hitl_required"] is True
    assert result.output["escalation_ref"]


@pytest.mark.asyncio
async def test_connector_degraded_state_downgrades_read_only_snapshot() -> None:
    result = await _agent().execute(
        _assignment("weekly_market_snapshot", _base_inputs(connector_read_ready=False))
    )

    assert result.status == "completed"
    assert result.output["status"] == "snapshot_degraded"
    assert result.output["degraded_reasons"]
    assert result.output["confidence"] < 0.8


@pytest.mark.asyncio
async def test_shadow_mode_cannot_write_competitive_response_externally() -> None:
    gateway = FakeToolGateway({("buffer", "publish_competitive_response"): {"status": "write_confirmed"}})
    result = await _agent(gateway).execute(
        _assignment(
            "publish_competitive_response",
            {"workflow_mode": "shadow", "connector_write_ready": True},
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "shadow_only"
    assert result.output["external_write_confirmation_status"] == "shadow_only"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_active_external_action_blocks_when_connector_not_write_safe() -> None:
    result = await _agent().execute(
        _assignment(
            "publish_competitive_response",
            {"workflow_mode": "active", "connector_write_ready": False, "approved": True, "escalation_ref": "esc-1"},
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "blocked"
    assert any("not write-safe" in reason for reason in result.output["blocked_reasons"])


@pytest.mark.asyncio
async def test_active_external_action_cannot_complete_without_confirmed_write() -> None:
    result = await _agent().execute(
        _assignment(
            "publish_competitive_response",
            {
                "workflow_mode": "active",
                "connector_write_ready": True,
                "approved": True,
                "escalation_ref": "esc-1",
                "external_write_result": {
                    "status": "accepted",
                    "connector_key": "buffer",
                    "idempotency_key": "competitive:write:1",
                },
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "write_unconfirmed"
    assert result.output["external_writes_completed"] is False
    assert result.output["blocked_reasons"]


@pytest.mark.asyncio
async def test_confirmed_competitive_write_has_policy_audit_escalation_and_write_ref() -> None:
    result = await _agent().execute(
        _assignment(
            "publish_competitive_response",
            {
                "workflow_mode": "active",
                "connector_write_ready": True,
                "approved": True,
                "escalation_ref": "esc-1",
                "external_write_result": {
                    "status": "write_confirmed",
                    "connector_key": "buffer",
                    "external_object_id": "post-123",
                    "idempotency_key": "competitive:write:1",
                    "confirmed_at": "2026-05-24T10:00:00Z",
                    "audit_ref": "audit-competitive-write-1",
                },
            },
        )
    )

    assert result.status == "completed"
    assert result.output["external_writes_completed"] is True
    assert result.output["external_write_ref"]["external_object_id"] == "post-123"
    assert result.output["audit_ref"].startswith("competitive_intel:")
    assert result.output["policy_ref"]
    assert result.output["escalation_ref"]


def test_competitive_intel_registered_and_contract_metadata_is_beta() -> None:
    assert AgentRegistry.get_by_type("competitive_intel") is CompetitiveIntelAgent
    spec = get_marketing_agent_contract_spec("competitive_intel")
    assert spec["maturity"] == "beta"
    assert spec["production_ready"] is False
    assert spec["surface"] == "core.agents.marketing.competitive_intel"


def test_workflow_linter_no_longer_treats_competitive_intel_as_unknown() -> None:
    result = lint_marketing_workflow(
        _workflow(_workflow_step("weekly_market_snapshot"), mode="target"),
        connector_contracts=[
            _write_contract(connector_key="brandwatch", category="Brand", write_ready=False),
            _write_contract(connector_key="ahrefs", category="SEO", write_ready=False),
        ],
    )
    codes = {finding.code for finding in result.findings}

    assert "marketing_agent_type_unknown" not in codes
    assert "marketing_agent_unavailable_target_only" not in codes


def test_production_workflow_blocks_competitive_external_action_without_write_proof() -> None:
    result = lint_marketing_workflow(
        _workflow(_workflow_step("publish_competitive_response", connector_key="buffer")),
        connector_contracts=[_write_contract(connector_key="buffer", category="Social")],
    )
    codes = {finding.code for finding in result.findings}

    assert "marketing_agent_type_unknown" not in codes
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_agent_beta_in_production" in codes
    assert "marketing_external_write_confirmation_metadata_missing" in codes
    assert "marketing_decision_audit_evidence_missing" in codes


def test_competitive_production_lint_passes_only_with_write_policy_audit_metadata() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _workflow_step(
                "publish_competitive_response",
                connector_key="buffer",
                external_write_confirmation_required=True,
                expected_confirmation_fields=["external_object_id", "confirmed_at"],
                idempotency_key_template="competitive:{competitor}:response",
                decision_audit_required=True,
            )
        ),
        connector_contracts=[_write_contract(connector_key="buffer", category="Social")],
    )
    codes = {finding.code for finding in result.findings}

    assert "marketing_external_write_confirmation_metadata_missing" not in codes
    assert "marketing_decision_audit_evidence_missing" not in codes
    assert "marketing_external_write_connector_not_ready" not in codes
    assert "marketing_agent_beta_in_production" in codes


def test_pilot_proof_keeps_competitive_intel_unproven_without_real_vendor_production_proof() -> None:
    proof = build_cmo_pilot_proof_projection(
        environment_type="real_vendor",
        agent_contracts=_proof_contracts(),
    )
    unproven = {item["capability_key"]: item for item in proof["cmo_pilot_proof"]["unproven_capabilities"]}
    proven = {item["capability_key"] for item in proof["cmo_pilot_proof"]["proven_capabilities"]}

    assert "agent:competitive_intel" not in proven
    assert unproven["agent:competitive_intel"]["status"] == "beta"
    assert proof["cmo_pilot_proof"]["full_cmo_autonomy_claim_allowed"] is False
