from __future__ import annotations

from typing import Any

import pytest

from core.agents.marketing.crm_intelligence import CrmIntelligenceAgent
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
        message_id=f"msg-crm-{action}",
        correlation_id="corr-crm",
        workflow_run_id="run-crm",
        workflow_definition_id="wf-crm",
        step_id=f"step-{action}",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(
            agent_id="agent-crm",
            agent_type="crm_intelligence",
            agent_token="test-token",
        ),
        task=TaskInput(action=action, inputs=inputs),
    )


def _agent(gateway: FakeToolGateway | None = None) -> CrmIntelligenceAgent:
    return CrmIntelligenceAgent(
        agent_id="agent-crm",
        tenant_id="tenant-cmo",
        tool_gateway=gateway,
        authorized_tools=[
            "hubspot.update_lifecycle_stage",
            "hubspot.update_lead_scores",
            "hubspot.update_segment_membership",
            "salesforce.update_target_accounts",
            "hubspot.bulk_crm_update",
        ],
    )


def _crm_inputs(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "deals": [
            {"id": "d1", "stage": "discovery", "days_in_stage": 5, "amount": 5000},
            {"id": "d2", "stage": "discovery", "days_in_stage": 7, "amount": 8000},
            {"id": "d3", "stage": "proposal", "days_in_stage": 9, "amount": 12000},
            {"id": "d4", "stage": "proposal", "days_in_stage": 10, "amount": 9000},
            {"id": "d5", "stage": "negotiation", "days_in_stage": 45, "amount": 30000},
            {"id": "d6", "stage": "negotiation", "days_in_stage": 60, "amount": 22000},
        ],
        "funnel_stage_counts": {
            "visit": 5000,
            "lead": 1200,
            "mql": 400,
            "sql": 90,
            "opportunity": 30,
        },
        "contacts": [
            {
                "id": "c1",
                "lead_score": 30.0,
                "firmographic_fit": 0.9,
                "title_seniority": "vp",
                "days_since_last_activity": 2,
                "engagement_depth": 0.8,
                "intent_score": 80,
                "demo_request": True,
                "industry": "Fintech",
                "company_size": "enterprise",
                "engagement_score": 85,
                "recent_engagement": True,
            },
            {
                "id": "c2",
                "lead_score": 10.0,
                "firmographic_fit": 0.2,
                "title_seniority": "ic",
                "days_since_last_activity": 60,
                "engagement_depth": 0.1,
                "intent_score": 5,
                "demo_request": False,
                "industry": "Retail",
                "company_size": "smb",
                "engagement_score": 20,
            },
            {
                "id": "c3",
                "lead_score": 40.0,
                "firmographic_fit": 0.7,
                "title_seniority": "director",
                "days_since_last_activity": 5,
                "engagement_depth": 0.5,
                "intent_score": 50,
                "demo_request": False,
                "industry": "Fintech",
                "company_size": "midmarket",
                "engagement_score": 55,
            },
        ],
        "accounts": [
            {
                "id": "acct_a",
                "mrr": 12000,
                "engagement_score": 90,
                "open_support_tickets": 0,
                "nps": 70,
                "payment_health": "ok",
                "days_since_last_login": 1,
                "usage_trend": "growing",
            },
            {
                "id": "acct_b",
                "mrr": 2500,
                "engagement_score": 10,
                "open_support_tickets": 9,
                "nps": -70,
                "payment_health": "overdue",
                "days_since_last_login": 45,
                "usage_trend": "declining",
            },
        ],
        "source_refs": [{"type": "hubspot", "ref_id": "tenant-cmo"}],
        "workflow_mode": "shadow",
    }
    payload.update(overrides)
    return payload


def _write_contract(**overrides: object) -> dict[str, Any]:
    contract: dict[str, Any] = {
        "connector_key": "hubspot",
        "category": "CRM",
        "read_ready": True,
        "write_ready": True,
        "write_safe": True,
        "idempotency_key_supported": True,
        "retry_budget": {
            "idempotency_key": "crm-write-001",
            "idempotency_supported": True,
            "remaining_attempts": 2,
        },
    }
    contract.update(overrides)
    return contract


def _workflow(step: dict[str, Any], *, mode: str = "production") -> dict[str, Any]:
    return {
        "id": "wf_crm",
        "name": "CRM Pipeline Intelligence",
        "domain": "marketing",
        "mode": mode,
        "steps": [step],
    }


def _workflow_step(action: str, **overrides: object) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "step_crm",
        "name": "CRM Step",
        "type": "agent",
        "agent_type": "crm_intelligence",
        "action": action,
    }
    step.update(overrides)
    return step


# ---------------------------------------------------------------------------
# Pipeline velocity / funnel conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_velocity_analysis_detects_bottlenecks() -> None:
    result = await _agent().execute(_assignment("pipeline_velocity_analysis", _crm_inputs()))
    velocity = result.output["pipeline_velocity"]

    assert result.output["status"] == "pipeline_velocity_analyzed"
    assert velocity["deal_count"] == 6
    assert velocity["avg_days_in_stage"]["discovery"] == pytest.approx(6.0)
    assert velocity["avg_days_in_stage"]["negotiation"] == pytest.approx(52.5)
    bottleneck_stages = {row["stage"] for row in velocity["bottlenecks"]}
    assert "negotiation" in bottleneck_stages
    assert any(action["action"] == "review_stuck_deals" for action in velocity["recommended_actions"])


@pytest.mark.asyncio
async def test_funnel_conversion_with_low_sample_and_safe_division() -> None:
    inputs = _crm_inputs(
        funnel_stage_counts={"visit": 0, "lead": 5, "mql": 1, "sql": 0},
    )
    result = await _agent().execute(_assignment("funnel_conversion_analysis", inputs))
    funnel = result.output["funnel_conversion"]

    assert funnel["conversion_rates"]["visit->lead"] is None
    assert funnel["conversion_rates"]["lead->mql"] == pytest.approx(0.20)
    assert funnel["sufficient_sample"] is False
    assert any(
        action["action"] == "increase_funnel_sample_size"
        for action in funnel["recommended_actions"]
    )


# ---------------------------------------------------------------------------
# Lead scoring / SQL promotion / segments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_scoring_refresh_is_deterministic_and_explainable() -> None:
    inputs = _crm_inputs()
    first = await _agent().execute(_assignment("lead_scoring_refresh", inputs))
    second = await _agent().execute(_assignment("lead_scoring_refresh", inputs))

    first_scoring = first.output["lead_scoring"]
    second_scoring = second.output["lead_scoring"]

    assert first_scoring["contacts"] == second_scoring["contacts"]
    top = first_scoring["contacts"][0]
    assert "explanation" in top
    assert sum(top["breakdown"].values()) == pytest.approx(top["score"], rel=1e-3)
    qualifications = {c["contact_id"]: c["qualification"] for c in first_scoring["contacts"]}
    assert qualifications["c1"] in {"mql", "sql"}
    assert qualifications["c2"] == "lead"


@pytest.mark.asyncio
async def test_sql_promotion_classification_uses_clear_criteria() -> None:
    inputs = _crm_inputs()
    result = await _agent().execute(_assignment("sql_promotion_criteria", inputs))
    promotion = result.output["sql_promotion"]

    ids_promotable = {entry["contact_id"] for entry in promotion["promotable"]}
    ids_non_promotable = {entry["contact_id"] for entry in promotion["non_promotable"]}
    assert "c1" in ids_promotable
    assert "c2" in ids_non_promotable
    c2_reasons = next(
        entry["reasons"] for entry in promotion["non_promotable"] if entry["contact_id"] == "c2"
    )
    assert any("intent" in reason or "engagement" in reason or "score" in reason for reason in c2_reasons)


@pytest.mark.asyncio
async def test_segment_recommendation_returns_action_plan() -> None:
    inputs = _crm_inputs()
    result = await _agent().execute(_assignment("segment_recommendation", inputs))
    segments = result.output["segments"]

    assert set(segments["by_behaviour"].keys()) >= {"high_intent", "warm", "dormant"}
    actions = {action["action"] for action in segments["recommended_actions"]}
    assert "enroll_in_nurture" in actions
    assert "win_back_campaign" in actions


# ---------------------------------------------------------------------------
# Churn risk / account health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_churn_risk_signals_identify_at_risk_accounts() -> None:
    inputs = _crm_inputs()
    result = await _agent().execute(_assignment("extract_churn_signals", inputs))
    churn = result.output["churn_signals"]

    risky_ids = {entry["account_id"] for entry in churn["at_risk_accounts"]}
    assert "acct_b" in risky_ids
    assert "acct_a" not in risky_ids
    assert churn["signal_summary"]


@pytest.mark.asyncio
async def test_account_deal_health_summary_orders_by_health() -> None:
    inputs = _crm_inputs()
    result = await _agent().execute(_assignment("account_health_summary", inputs))
    accounts = result.output["account_health"]["accounts"]
    scores = {entry["account_id"]: entry["health_score"] for entry in accounts}

    assert scores["acct_a"] > scores["acct_b"]
    unhealthy = {entry["account_id"] for entry in result.output["account_health"]["unhealthy_accounts"]}
    assert "acct_b" in unhealthy
    assert "acct_a" not in unhealthy


# ---------------------------------------------------------------------------
# Degraded / missing mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_partial_or_missing_mapping_downgrades_or_blocks() -> None:
    inputs = _crm_inputs(
        connector_read_ready=False,
        stale_data=True,
        partial_data=True,
        mapping_status="invalid",
    )
    result = await _agent().execute(_assignment("pipeline_velocity_analysis", inputs))

    assert result.output["status"] == "pipeline_velocity_degraded"
    assert result.output["degraded_reasons"]
    assert result.output["confidence"] < 0.85


# ---------------------------------------------------------------------------
# Write safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_mode_cannot_write_externally() -> None:
    gateway = FakeToolGateway({})
    result = await _agent(gateway).execute(
        _assignment(
            "update_lifecycle_stage",
            _crm_inputs(
                workflow_mode="shadow",
                contact_ids=["c1"],
                lifecycle_stage="mql",
                affected_contacts=1,
            ),
        )
    )

    assert result.output["status"] == "shadow_only"
    assert result.output["external_write_confirmation_status"] == "shadow_only"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_active_crm_write_requires_policy_approval_audit_and_write_readiness() -> None:
    result = await _agent().execute(
        _assignment(
            "update_lead_scores",
            _crm_inputs(
                workflow_mode="active",
                connector_contract=_write_contract(),
                contact_ids=["c1", "c2"],
                affected_contacts=2,
                lead_score_updates={"c1": 78, "c2": 22},
            ),
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "blocked"
    assert result.output["policy_result"]["decision"] == "requires_approval"
    assert result.output["approval_required"] is True
    assert result.output["audit_ref"].startswith("crm_intelligence:")
    assert result.output["external_write_confirmation_status"] == "write_unconfirmed"


@pytest.mark.asyncio
async def test_target_account_list_change_above_threshold_requires_approval() -> None:
    result = await _agent().execute(
        _assignment(
            "change_target_accounts",
            _crm_inputs(
                workflow_mode="active",
                connector_contract=_write_contract(connector_key="salesforce"),
                affected_accounts=100,
                target_account_delta=100,
            ),
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["policy_result"]["decision"] in {"requires_approval", "requires_escalation"}
    assert result.output["approval_required"] is True
    assert result.output["escalation_ref"]


@pytest.mark.asyncio
async def test_connector_not_write_safe_blocks_active_write() -> None:
    result = await _agent().execute(
        _assignment(
            "update_lifecycle_stage",
            _crm_inputs(
                workflow_mode="active",
                approved=True,
                connector_contract=_write_contract(write_ready=False, write_safe=False),
                contact_ids=["c1"],
                affected_contacts=1,
            ),
        )
    )

    assert result.output["status"] == "blocked"
    assert any("not write-safe" in reason for reason in result.output["blocked_reasons"])


@pytest.mark.asyncio
async def test_active_crm_write_cannot_complete_without_confirmed_external_write() -> None:
    result = await _agent().execute(
        _assignment(
            "update_lead_scores",
            _crm_inputs(
                workflow_mode="active",
                approved=True,
                connector_contract=_write_contract(),
                contact_ids=["c1"],
                affected_contacts=1,
                external_write_result={
                    "connector_key": "hubspot",
                    "status": "accepted",
                    "idempotency_key": "crm:score:1",
                },
            ),
        )
    )

    assert result.output["status"] == "write_unconfirmed"
    assert result.output["external_writes_completed"] is False
    assert result.status == "hitl_triggered"


@pytest.mark.asyncio
async def test_confirmed_crm_write_emits_policy_audit_escalation_refs_when_required() -> None:
    result = await _agent().execute(
        _assignment(
            "change_target_accounts",
            _crm_inputs(
                workflow_mode="active",
                approved=True,
                escalation_ref="esc-target-accounts",
                connector_contract=_write_contract(connector_key="salesforce"),
                affected_accounts=120,
                target_account_delta=120,
                external_write_result={
                    "connector_key": "salesforce",
                    "status": "write_confirmed",
                    "external_object_id": "list-007",
                    "source_url": "https://crm.example/list-007",
                    "idempotency_key": "crm:targets:1",
                    "confirmed_at": "2026-05-24T12:00:00Z",
                    "audit_ref": "audit-crm-write",
                },
            ),
        )
    )
    contract = build_marketing_agent_contract_output(
        "crm_intelligence",
        "change_target_accounts",
        result=result,
        audit_ref=result.output["audit_ref"],
        external_write_confirmation_status=result.output["external_write_confirmation_status"],
    )

    assert result.status == "completed"
    assert result.output["policy_result"]["decision"] in {"requires_approval", "requires_escalation"}
    assert result.output["escalation_ref"]
    assert result.output["external_write_ref"]["external_object_id"] == "list-007"
    assert contract_has_required_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["external_writes_completed"] is True


# ---------------------------------------------------------------------------
# Contract / workflow linter / pilot proof
# ---------------------------------------------------------------------------


def test_contract_workflow_linter_activation_and_pilot_proof_are_truthful_beta() -> None:
    read_lint = lint_marketing_workflow(_workflow(_workflow_step("pipeline_velocity_analysis"), mode="target"))
    write_lint = lint_marketing_workflow(
        _workflow(_workflow_step("update_lifecycle_stage", connector_key="hubspot")),
        connector_contracts=[_write_contract()],
    )
    proof = build_cmo_pilot_proof_projection(
        environment_type="vendor_sandbox",
        source_context={"source": "vendor_sandbox"},
        agent_contracts=[get_marketing_agent_contract_spec("crm_intelligence")],
    )["cmo_pilot_proof"]
    contract = build_marketing_agent_contract_output(
        "crm_intelligence",
        "pipeline_velocity_analysis",
        audit_ref="audit-crm-contract",
    )

    assert contract_has_required_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False
    assert "marketing_agent_type_unknown" not in {finding.code for finding in read_lint.findings}
    assert "marketing_agent_stub_target_only" not in {finding.code for finding in read_lint.findings}
    assert "marketing_agent_stub_for_production" not in {finding.code for finding in write_lint.findings}
    assert "marketing_agent_beta_in_production" in {finding.code for finding in write_lint.findings}
    assert proof["production_claim_allowed"] is False
    assert any(item["capability_key"] == "agent:crm_intelligence" for item in proof["unproven_capabilities"])
