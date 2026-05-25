from __future__ import annotations

from typing import Any

import pytest

from core.agents.marketing.abm_agent import ABMAgent
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
        message_id=f"msg-abm-{action}",
        correlation_id="corr-abm",
        workflow_run_id="run-abm",
        workflow_definition_id="wf-abm",
        step_id=f"step-{action}",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(
            agent_id="agent-abm",
            agent_type="abm",
            agent_token="test-token",
        ),
        task=TaskInput(action=action, inputs=inputs),
    )


def _agent(gateway: FakeToolGateway | None = None) -> ABMAgent:
    return ABMAgent(
        agent_id="agent-abm",
        tenant_id="tenant-cmo",
        tool_gateway=gateway,
        authorized_tools=["hubspot.update_target_accounts", "linkedin_ads.create_abm_campaign"],
    )


def _accounts() -> list[dict[str, Any]]:
    return [
        {
            "company_name": "Alpha Bank",
            "domain": "alpha.example",
            "industry": "financial_services",
            "employee_count": 1200,
            "annual_revenue": 250000000,
            "region": "apac",
            "tier": "strategic",
            "bombora": 92,
            "g2": 84,
            "trustradius": 78,
            "crm": 74,
            "website_visits": 80,
            "email_clicks": 14,
            "form_submissions": 3,
            "meetings": 1,
        },
        {
            "company_name": "Beta Manufacturing",
            "domain": "beta.example",
            "industry": "manufacturing",
            "employee_count": 250,
            "annual_revenue": 35000000,
            "region": "emea",
            "tier": "commercial",
            "bombora": 40,
            "g2": 20,
            "trustradius": 30,
            "crm": 50,
        },
    ]


def _icp() -> dict[str, Any]:
    return {
        "target_industries": ["financial_services", "software"],
        "employee_min": 500,
        "employee_max": 5000,
        "revenue_min": 100000000,
        "regions": ["apac", "amer"],
        "tiers": ["strategic", "enterprise"],
    }


def _write_contract(**overrides: object) -> dict[str, Any]:
    contract = {
        "connector_key": "hubspot",
        "category": "CRM",
        "read_ready": True,
        "write_ready": True,
        "write_safe": True,
        "idempotency_key_supported": True,
        "retry_budget": {
            "idempotency_key": "abm:write:001",
            "idempotency_supported": True,
            "remaining_attempts": 2,
        },
    }
    contract.update(overrides)
    return contract


def _workflow(step: dict[str, Any], *, mode: str = "production") -> dict[str, Any]:
    return {
        "id": "wf_abm",
        "name": "ABM Sprint",
        "domain": "marketing",
        "mode": mode,
        "steps": [step],
    }


def _workflow_step(action: str, **overrides: object) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "step_abm",
        "name": "ABM Step",
        "type": "agent",
        "agent_type": "abm_agent",
        "action": action,
    }
    step.update(overrides)
    return step


@pytest.mark.asyncio
async def test_abm_agent_happy_path_account_scoring_has_contract_shape() -> None:
    result = await _agent().execute(
        _assignment(
            "score_accounts",
            {"accounts": _accounts(), "icp": _icp(), "high_intent_threshold": 80},
        )
    )
    contract = build_marketing_agent_contract_output(
        "abm_agent",
        "score_accounts",
        result=result,
        audit_ref=result.output["audit_ref"],
    )

    assert result.status == "completed"
    assert result.output["status"] == "accounts_scored"
    assert result.output["account_scores"][0]["domain"] == "alpha.example"
    assert result.output["account_scores"][0]["score_band"] == "hot"
    assert result.output["intent_alerts"]
    assert contract_has_required_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False


@pytest.mark.asyncio
async def test_abm_configurable_bombora_g2_trustradius_crm_weighting_math() -> None:
    result = await _agent().execute(
        _assignment(
            "score_intent_heat",
            {
                "accounts": [
                    {
                        "company_name": "Weighted Co",
                        "domain": "weighted.example",
                        "bombora": 100,
                        "g2": 0,
                        "trustradius": 0,
                        "crm": 0,
                    }
                ],
                "source_weights": {"bombora": 1.0, "g2": 0.0, "trustradius": 0.0, "crm": 0.0},
            },
        )
    )

    row = result.output["account_scores"][0]
    assert result.status == "completed"
    assert row["intent_heat_score"] == 100
    assert result.output["source_weights"] == {"bombora": 1.0, "g2": 0.0, "trustradius": 0.0, "crm": 0.0}


@pytest.mark.asyncio
async def test_abm_icp_fit_scoring_is_deterministic() -> None:
    result = await _agent().execute(
        _assignment("score_icp_fit", {"accounts": _accounts(), "icp": _icp()})
    )

    by_domain = {row["domain"]: row for row in result.output["account_scores"]}
    assert by_domain["alpha.example"]["icp_fit_score"] == 100
    assert by_domain["beta.example"]["icp_fit_score"] < 50
    assert "industry_matches_icp" in by_domain["alpha.example"]["icp_fit_reasons"]


@pytest.mark.asyncio
async def test_abm_deterministic_next_best_action_output() -> None:
    result = await _agent().execute(
        _assignment(
            "recommend_next_best_action",
            {"accounts": _accounts(), "icp": _icp()},
        )
    )

    actions = {row["account_domain"]: row["action"] for row in result.output["recommended_actions"]}
    assert result.status == "completed"
    assert actions["alpha.example"] == "create_sales_alert"
    assert actions["beta.example"] in {"monitor_intent", "add_to_nurture_segment", "route_to_sdr_research"}


@pytest.mark.asyncio
async def test_abm_csv_account_ingest_validation_happy_path() -> None:
    result = await _agent().execute(
        _assignment(
            "validate_account_csv",
            {
                "csv_content": (
                    "company_name,domain,industry,tier\n"
                    "Alpha Bank,alpha.example,financial_services,strategic\n"
                    "Beta Manufacturing,beta.example,manufacturing,commercial\n"
                )
            },
        )
    )

    assert result.status == "completed"
    assert result.output["status"] == "csv_validated"
    assert len(result.output["valid_accounts"]) == 2
    assert result.output["invalid_rows"] == []


@pytest.mark.asyncio
async def test_abm_csv_validation_rejects_missing_required_fields_and_malformed_rows() -> None:
    missing_header = await _agent().execute(
        _assignment("validate_account_csv", {"csv_content": "company_name,industry\nAlpha Bank,finance\n"})
    )
    malformed_row = await _agent().execute(
        _assignment(
            "validate_account_csv",
            {"csv_content": "company_name,domain\nAlpha Bank,not-a-domain\nMissing Domain,\n"},
        )
    )

    assert missing_header.status == "hitl_triggered"
    assert missing_header.output["status"] == "blocked"
    assert "domain" in missing_header.output["missing_headers"]
    assert malformed_row.status == "hitl_triggered"
    assert len(malformed_row.output["invalid_rows"]) == 2


@pytest.mark.asyncio
async def test_high_intent_account_creates_alert_and_recommendation() -> None:
    result = await _agent().execute(
        _assignment(
            "score_accounts",
            {
                "accounts": _accounts(),
                "icp": _icp(),
                "high_intent_threshold": 80,
            },
        )
    )

    assert result.output["intent_alerts"][0]["account_domain"] == "alpha.example"
    assert result.output["recommended_actions"][0]["action"] == "review_high_intent_alerts"


@pytest.mark.asyncio
async def test_target_account_list_change_above_threshold_requires_approval() -> None:
    result = await _agent().execute(
        _assignment(
            "update_target_accounts",
            {
                "workflow_mode": "active",
                "accounts": _accounts(),
                "target_account_delta": 30,
                "connector_contract": _write_contract(),
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "blocked"
    assert result.output["approval_required"] is True
    assert result.output["policy_result"]["decision"] == "requires_approval"
    assert result.output["policy_ref"]
    assert result.output["audit_ref"].startswith("abm:")


@pytest.mark.asyncio
async def test_abm_budget_action_requires_policy_approval_and_escalation_ref() -> None:
    result = await _agent().execute(
        _assignment(
            "launch_abm_campaign",
            {
                "workflow_mode": "active",
                "accounts": _accounts(),
                "budget_amount": 50000,
                "connector_contract": _write_contract(connector_key="linkedin_ads", category="Ads"),
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["approval_required"] is True
    assert result.output["policy_result"]["decision"] == "requires_approval"
    assert result.output["escalation_ref"]
    assert result.output["audit_ref"].startswith("abm:")


@pytest.mark.asyncio
async def test_shadow_mode_cannot_write_abm_externally() -> None:
    gateway = FakeToolGateway({("hubspot", "update_target_accounts"): {"status": "write_confirmed"}})
    result = await _agent(gateway).execute(
        _assignment(
            "update_target_accounts",
            {
                "workflow_mode": "shadow",
                "accounts": _accounts(),
                "connector_contract": _write_contract(),
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "shadow_only"
    assert result.output["external_writes_completed"] is False
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_active_external_action_blocks_when_connector_is_not_write_safe() -> None:
    result = await _agent().execute(
        _assignment(
            "update_target_accounts",
            {
                "workflow_mode": "active",
                "accounts": _accounts(),
                "approved": True,
                "connector_contract": _write_contract(write_ready=False, write_safe=False),
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "blocked"
    assert any("not write-safe" in reason for reason in result.output["blocked_reasons"])


@pytest.mark.asyncio
async def test_active_external_action_cannot_complete_without_confirmed_write_evidence() -> None:
    gateway = FakeToolGateway(
        {("hubspot", "update_target_accounts"): {"status": "accepted", "external_object_id": "list-accepted"}}
    )
    result = await _agent(gateway).execute(
        _assignment(
            "update_target_accounts",
            {
                "workflow_mode": "active",
                "accounts": _accounts(),
                "approved": True,
                "approval_ref": "approval-abm-1",
                "connector_contract": _write_contract(),
                "idempotency_key": "abm:list:update:1",
            },
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["external_write_confirmation_status"] == "accepted"
    assert result.output["external_writes_completed"] is False
    assert any("unconfirmed" in reason for reason in result.output["blocked_reasons"])


@pytest.mark.asyncio
async def test_confirmed_abm_external_action_has_policy_audit_and_write_ref() -> None:
    gateway = FakeToolGateway(
        {
            ("hubspot", "update_target_accounts"): {
                "status": "write_confirmed",
                "connector_key": "hubspot",
                "external_object_id": "target-list-1",
                "source_url": "https://crm.example/lists/1",
                "idempotency_key": "abm:list:update:1",
                "confirmed_at": "2026-05-24T12:00:00+00:00",
                "audit_ref": "audit-abm-write-1",
            }
        }
    )
    result = await _agent(gateway).execute(
        _assignment(
            "update_target_accounts",
            {
                "workflow_mode": "active",
                "accounts": _accounts(),
                "approved": True,
                "approval_ref": "approval-abm-1",
                "connector_contract": _write_contract(),
                "idempotency_key": "abm:list:update:1",
            },
        )
    )

    assert result.status == "completed"
    assert result.output["external_write_confirmation_status"] == "write_confirmed"
    assert result.output["external_writes_completed"] is True
    assert result.output["external_write_ref"]["external_object_id"] == "target-list-1"
    assert result.output["audit_ref"].startswith("abm:")


def test_abm_registered_and_contract_metadata_is_beta() -> None:
    assert AgentRegistry.get_by_type("abm") is ABMAgent
    spec = get_marketing_agent_contract_spec("abm_agent")
    assert spec["agent_type"] == "abm"
    assert spec["maturity"] == "beta"
    assert spec["production_ready"] is False
    assert spec["surface"] == "core.agents.marketing.abm_agent"
    assert "score_accounts" in spec["actions"]


def test_workflow_linter_no_longer_treats_abm_as_unknown_but_blocks_unsafe_production_write() -> None:
    recommendation = lint_marketing_workflow(
        _workflow(_workflow_step("score_accounts")),
        connector_contracts=[
            _write_contract(connector_key="hubspot", category="CRM"),
            _write_contract(connector_key="bombora", category="ABM", write_ready=False, write_safe=False),
        ],
    )
    unsafe_write = lint_marketing_workflow(
        _workflow(_workflow_step("update_target_accounts")),
        connector_contracts=[
            _write_contract(connector_key="hubspot", category="CRM"),
            _write_contract(connector_key="bombora", category="ABM", write_ready=False, write_safe=False),
        ],
    )

    recommendation_codes = {finding.code for finding in recommendation.findings}
    unsafe_codes = {finding.code for finding in unsafe_write.findings}
    assert "marketing_agent_type_unknown" not in recommendation_codes
    assert "marketing_agent_unavailable_for_production" not in recommendation_codes
    assert "marketing_agent_beta_in_production" in recommendation_codes
    assert unsafe_write.has_errors is True
    assert "marketing_external_write_confirmation_metadata_missing" in unsafe_codes
    assert "marketing_decision_audit_evidence_missing" in unsafe_codes


def test_abm_production_lint_passes_only_with_write_policy_audit_metadata() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _workflow_step(
                "update_target_accounts",
                external_write_confirmation_required=True,
                expected_confirmation_fields=["external_object_id", "confirmed_at"],
                idempotency_key_template="abm:{account_list_id}:update",
                decision_audit_required=True,
                approval_timeout_policy={"approval_type": "target_account_list_change"},
            )
        ),
        connector_contracts=[
            _write_contract(connector_key="hubspot", category="CRM"),
            _write_contract(connector_key="bombora", category="ABM", write_ready=False, write_safe=False),
        ],
    )

    codes = {finding.code for finding in result.findings}
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_external_write_connector_not_ready" not in codes
    assert "marketing_external_write_confirmation_metadata_missing" not in codes


def test_pilot_proof_keeps_abm_unproven_without_real_vendor_production_proof() -> None:
    projection = build_cmo_pilot_proof_projection(
        environment_type="vendor_sandbox",
        source_context={"tenant_id": "tenant-cmo", "source": "vendor_sandbox"},
        connector_setup=[],
        connector_contracts=[],
        agent_contracts=[get_marketing_agent_contract_spec("abm")],
    )
    proof = projection["cmo_pilot_proof"]
    proven = {row["capability_key"] for row in proof["proven_capabilities"]}
    unproven = {row["capability_key"]: row for row in proof["unproven_capabilities"]}

    assert "agent:abm" not in proven
    assert unproven["agent:abm"]["status"] == "beta"
    assert proof["production_claim_allowed"] is False
