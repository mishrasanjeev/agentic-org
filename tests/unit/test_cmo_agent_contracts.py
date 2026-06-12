from __future__ import annotations

from typing import Any

import pytest

from core.agents.marketing.campaign_pilot import CampaignPilotAgent
from core.agents.marketing.content_factory import ContentFactoryAgent
from core.llm.router import LLMResponse, llm_router
from core.marketing.agent_contracts import (
    build_marketing_agent_contract_output,
    contract_has_required_shape,
    get_marketing_agent_contract_spec,
    marketing_agent_contract_specs,
)
from core.marketing.policy_manifest import evaluate_marketing_policy
from core.marketing.workflow_activation import build_cmo_workflow_activation
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


def _assignment(
    agent_type: str,
    action: str,
    inputs: dict[str, Any],
) -> TaskAssignment:
    return TaskAssignment(
        message_id=f"msg-{agent_type}",
        correlation_id=f"corr-{agent_type}",
        workflow_run_id="run-cmo-agent-contract",
        workflow_definition_id="wf-cmo-agent-contract",
        step_id=f"step-{agent_type}",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(
            agent_id=f"agent-{agent_type}",
            agent_type=agent_type,
            agent_token="test-token",
        ),
        task=TaskInput(action=action, inputs=inputs),
    )


def _campaign_agent(
    gateway: FakeToolGateway,
) -> CampaignPilotAgent:
    return CampaignPilotAgent(
        agent_id="agent-campaign-pilot",
        tenant_id="tenant-cmo",
        tool_gateway=gateway,
        authorized_tools=["google_ads.get_campaign_performance", "google_ads.pause_campaign"],
    )


def _content_agent(
    gateway: FakeToolGateway | None = None,
) -> ContentFactoryAgent:
    return ContentFactoryAgent(
        agent_id="agent-content-factory",
        tenant_id="tenant-cmo",
        tool_gateway=gateway,
        authorized_tools=["wordpress.create_draft", "sendgrid.create_campaign"],
    )


def _workflow(agent_type: str, action: str, *, mode: str = "production") -> dict[str, Any]:
    return {
        "id": "wf_contract_test",
        "name": "CMO Contract Test",
        "domain": "marketing",
        "mode": mode,
        "steps": [
            {
                "id": "step_1",
                "name": "Step 1",
                "type": "agent",
                "agent_type": agent_type,
                "action": action,
            }
        ],
    }


async def _fake_llm_complete(
    messages: list[dict[str, str]],
    model_override: str | None = None,
    temperature: float | None = None,
    max_tokens: int = 4096,
) -> LLMResponse:
    del messages, model_override, temperature, max_tokens
    content = " ".join(["growth content strategy"] * 40)
    return LLMResponse(content=content, model="test-llm", tokens_used=12)


def _assert_contract_shape(contract: dict[str, Any]) -> None:
    assert contract_has_required_shape(contract)
    assert isinstance(contract["status"], str)
    assert isinstance(contract["confidence"], float | int)
    assert isinstance(contract["rationale"], str)
    assert isinstance(contract["recommended_actions"], list)
    assert isinstance(contract["source_refs"], list)
    assert isinstance(contract["policy_result"], dict)
    assert isinstance(contract["approval_required"], bool)
    assert isinstance(contract["hitl_required"], bool)
    assert isinstance(contract["degraded_reasons"], list)
    assert isinstance(contract["blocked_reasons"], list)


def test_contract_inventory_covers_all_cmo_marketing_agent_surfaces() -> None:
    specs = {row["agent_type"]: row for row in marketing_agent_contract_specs()}

    expected = {
        "campaign_pilot",
        "content_factory",
        "email_marketing",
        "brand_monitor",
        "seo_strategist",
        "crm_intelligence",
        "social_media",
        "abm",
        "competitive_intel",
    }
    assert expected.issubset(specs)
    assert get_marketing_agent_contract_spec("email_agent")["agent_type"] == "email_marketing"
    assert specs["campaign_pilot"]["maturity"] == "production"
    assert specs["content_factory"]["maturity"] == "beta"
    assert specs["email_marketing"]["maturity"] == "beta"
    assert specs["brand_monitor"]["maturity"] == "beta"
    assert specs["seo_strategist"]["maturity"] == "beta"
    assert specs["crm_intelligence"]["maturity"] == "beta"
    assert specs["social_media"]["maturity"] == "beta"
    assert specs["abm"]["maturity"] == "beta"
    assert specs["competitive_intel"]["maturity"] == "beta"
    assert all(not specs[key]["production_ready"] for key in expected - {"campaign_pilot"})


def test_all_agent_contract_outputs_have_required_shape_or_blocker() -> None:
    for spec in marketing_agent_contract_specs():
        action = spec["actions"][0]
        contract = build_marketing_agent_contract_output(spec["agent_type"], action)

        _assert_contract_shape(contract)
        if spec["maturity"] in {"stub", "unavailable"}:
            assert contract["production_ready"] is False
            assert contract["blocked_reasons"]
            assert contract["audit_ref"] is None


@pytest.mark.asyncio
async def test_campaign_pilot_happy_path_contract_shape() -> None:
    gateway = FakeToolGateway(
        {
            ("google_ads", "get_campaign_performance"): {
                "spend": 100,
                "revenue": 220,
                "impressions": 1000,
                "clicks": 100,
                "conversions": 10,
            }
        }
    )
    result = await _campaign_agent(gateway).execute(
        _assignment(
            "campaign_pilot",
            "manage_campaign",
            {
                "campaign_id": "cmp-1",
                "campaign_name": "Pipeline Sprint",
                "budget": 1000,
                "channels": ["google_ads"],
                "channel_budgets": {"google_ads": 500},
            },
        )
    )

    contract = build_marketing_agent_contract_output(
        "campaign_pilot",
        "manage_campaign",
        result=result,
        audit_ref=result.output["audit_ref"],
    )

    assert result.status == "completed"
    assert result.output["hitl_required"] is False
    assert result.output["source_refs"]
    _assert_contract_shape(contract)
    assert contract["status"] == "completed"
    assert contract["confidence"] >= 0.85
    assert contract["blocked_reasons"] == []


@pytest.mark.asyncio
async def test_campaign_pilot_policy_and_hitl_path_requires_approval() -> None:
    gateway = FakeToolGateway(
        {
            ("google_ads", "get_campaign_performance"): {
                "spend": 100,
                "revenue": 200,
                "impressions": 1000,
                "clicks": 100,
                "conversions": 10,
            }
        }
    )
    result = await _campaign_agent(gateway).execute(
        _assignment(
            "campaign_pilot",
            "create",
            {
                "campaign_name": "Enterprise Launch",
                "budget": 600000,
                "channels": ["google_ads"],
            },
        )
    )

    contract = build_marketing_agent_contract_output(
        "campaign_pilot",
        "create",
        result=result,
        audit_ref=result.output["audit_ref"],
    )

    assert result.status == "hitl_triggered"
    assert result.hitl_request is not None
    assert result.output["policy_result"]["decision"] == "requires_approval"
    assert contract["approval_required"] is True
    assert contract["hitl_required"] is True


@pytest.mark.asyncio
async def test_campaign_pilot_does_not_complete_external_write_without_confirmation() -> None:
    gateway = FakeToolGateway(
        {
            ("google_ads", "get_campaign_performance"): {
                "spend": 100,
                "revenue": 50,
                "impressions": 1000,
                "clicks": 100,
                "conversions": 4,
            },
            ("google_ads", "pause_campaign"): {
                "status": "accepted",
                "external_object_id": "campaign-pause-1",
            },
        }
    )
    result = await _campaign_agent(gateway).execute(
        _assignment(
            "campaign_pilot",
            "manage_campaign",
            {
                "campaign_id": "cmp-low-roas",
                "campaign_name": "Low ROAS Campaign",
                "budget": 1000,
                "channels": ["google_ads"],
                "channel_budgets": {"google_ads": 500},
            },
        )
    )
    contract = build_marketing_agent_contract_output(
        "campaign_pilot",
        "pause_campaign",
        result=result,
        audit_ref=result.output["audit_ref"],
    )

    assert result.status == "hitl_triggered"
    assert result.output["external_write_confirmation_status"] == "write_unconfirmed"
    assert result.output["external_writes_completed"] is False
    assert result.output["actions_taken"][0]["success"] is False
    assert "write confirmation" in result.output["blocked_reasons"][0]
    assert contract["external_writes_completed"] is False
    assert contract["blocked_reasons"]


@pytest.mark.asyncio
async def test_campaign_pilot_invalid_input_has_failed_contract() -> None:
    result = await _campaign_agent(FakeToolGateway({})).execute(
        _assignment(
            "campaign_pilot",
            "manage_campaign",
            {
                "campaign_name": "Broken Campaign",
                "budget": "not-a-number",
                "channels": ["google_ads"],
            },
        )
    )
    contract = build_marketing_agent_contract_output(
        "campaign_pilot",
        "manage_campaign",
        result=result,
    )

    assert result.status == "failed"
    assert result.error and result.error["code"] == "CAMPAIGN_ERR"
    _assert_contract_shape(contract)
    assert "Decision audit evidence is missing." in contract["blocked_reasons"]


@pytest.mark.asyncio
async def test_content_factory_happy_path_contract_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_router, "complete", _fake_llm_complete)
    result = await _content_agent().execute(
        _assignment(
            "content_factory",
            "generate_content",
            {
                "content_type": "social",
                "topic": "Demand generation operating cadence",
                "target_audience": "CMOs",
                "keywords": ["growth"],
            },
        )
    )
    contract = build_marketing_agent_contract_output(
        "content_factory",
        "generate_content",
        result=result,
        audit_ref=result.output["audit_ref"],
    )

    assert result.status == "completed"
    assert result.output["approval_required"] is False
    assert result.output["policy_result"]["decision"] == "allowed"
    _assert_contract_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["blocked_reasons"] == []


@pytest.mark.asyncio
async def test_content_factory_publish_approval_path_creates_draft_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_router, "complete", _fake_llm_complete)
    gateway = FakeToolGateway(
        {
            ("wordpress", "create_draft"): {
                "status": "draft_created",
                "id": "post-1",
                "url": "https://cms.example.test/post-1",
            }
        }
    )
    result = await _content_agent(gateway).execute(
        _assignment(
            "content_factory",
            "publish_to_wordpress",
            {
                "content_type": "blog",
                "topic": "Pipeline quality report",
                "target_audience": "CMOs",
                "keywords": ["growth"],
                "publish_date": "2026-05-25T10:00:00+00:00",
                "channel": "website",
            },
        )
    )
    contract = build_marketing_agent_contract_output(
        "content_factory",
        "publish_to_wordpress",
        result=result,
        audit_ref=result.output["audit_ref"],
    )

    assert result.status == "hitl_triggered"
    assert result.output["approval_required"] is True
    assert result.output["publish_state"] == "draft_created_pending_approval"
    assert result.output["external_writes_completed"] is False
    assert contract["approval_required"] is True
    assert contract["external_writes_completed"] is False


@pytest.mark.asyncio
async def test_content_factory_degraded_connector_path_is_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_router, "complete", _fake_llm_complete)
    gateway = FakeToolGateway(
        {
            ("wordpress", "create_draft"): RuntimeError("cms timeout"),
        }
    )
    result = await _content_agent(gateway).execute(
        _assignment(
            "content_factory",
            "schedule_content",
            {
                "content_type": "blog",
                "topic": "Degraded CMS campaign",
                "target_audience": "CMOs",
                "keywords": ["growth"],
                "publish_date": "2026-05-25T10:00:00+00:00",
                "channel": "website",
            },
        )
    )
    contract = build_marketing_agent_contract_output(
        "content_factory",
        "schedule_content",
        result=result,
        audit_ref=result.output["audit_ref"],
        connector_ready=False,
    )

    assert result.status == "hitl_triggered"
    assert result.output["scheduled"] is False
    assert result.output["degraded_reasons"]
    assert "Required connector is unavailable or degraded." in contract["degraded_reasons"]


def test_email_marketing_contract_requires_approval_for_sends_and_unsubscribe_risk() -> None:
    policy = evaluate_marketing_policy(
        {
            "workflow_id": "lead_nurture",
            "action": "send_email",
            "workflow_mode": "active",
            "audience_size": 75000,
            "risk_flags": ["unsubscribe_rate_above_policy"],
            "external_write_required": True,
        }
    )
    contract = build_marketing_agent_contract_output(
        "email_agent",
        "send_email",
        policy_result={
            **policy,
            "reason": "Unsubscribe risk and large audience require CMO approval.",
        },
        audit_ref="audit-email-send-contract",
        external_write_confirmation_status="write_unconfirmed",
    )

    _assert_contract_shape(contract)
    assert contract["agent_type"] == "email_marketing"
    assert contract["maturity"] == "beta"
    assert contract["approval_required"] is True
    assert contract["policy_result"]["decision"] == "requires_approval"
    assert "Unsubscribe risk" in contract["policy_result"]["reason"]
    assert contract["external_writes_completed"] is False


def test_crm_intelligence_is_beta_not_stub_but_not_production_ready() -> None:
    """CMO-4.3 promoted CRM Intelligence out of the stub state."""

    contract = build_marketing_agent_contract_output(
        "crm_intelligence",
        "pipeline_velocity_analysis",
        audit_ref="audit-crm-intelligence-contract",
    )
    lint = lint_marketing_workflow(_workflow("crm_intelligence", "pipeline_velocity_analysis"))
    codes = {finding.code for finding in lint.findings}

    _assert_contract_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False
    assert "marketing_agent_stub_for_production" not in codes
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_agent_beta_in_production" in codes


def test_competitive_intel_is_beta_not_unavailable_but_not_production_ready() -> None:
    contract = build_marketing_agent_contract_output(
        "competitive_intel",
        "weekly_market_snapshot",
        audit_ref="audit-competitive-contract",
    )
    lint = lint_marketing_workflow(_workflow("competitive_intel", "weekly_market_snapshot"))
    codes = {finding.code for finding in lint.findings}

    _assert_contract_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_agent_beta_in_production" in codes


def test_brand_monitor_is_beta_not_stub_but_not_production_ready() -> None:
    contract = build_marketing_agent_contract_output(
        "brand_monitor",
        "detect_crisis",
        audit_ref="audit-brand-monitor-contract",
    )
    lint = lint_marketing_workflow(_workflow("brand_monitor", "detect_crisis"))
    codes = {finding.code for finding in lint.findings}

    _assert_contract_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False
    assert "marketing_agent_stub_for_production" not in codes
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_agent_beta_in_production" in codes


def test_seo_strategist_is_beta_not_stub_but_not_production_ready() -> None:
    contract = build_marketing_agent_contract_output(
        "seo_strategist",
        "keyword_gap_analysis",
        audit_ref="audit-seo-strategist-contract",
    )
    lint = lint_marketing_workflow(_workflow("seo_strategist", "keyword_gap_analysis"))
    codes = {finding.code for finding in lint.findings}

    _assert_contract_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False
    assert "marketing_agent_stub_for_production" not in codes
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_agent_beta_in_production" in codes


def test_workflow_activation_keeps_missing_agent_workflows_blocked_or_unavailable() -> None:
    activation = build_cmo_workflow_activation(
        connector_setup=[],
        data_readiness={"field_mapping_status": [], "backfill_status": []},
        connector_configs=[],
        connector_contracts=[],
    )
    rows = {row["workflow_key"]: row for row in activation["workflow_activation_status"]}

    assert rows["abm_sprint"]["state"] == "promotion_blocked"
    assert rows["competitive_intel_monitoring"]["state"] == "promotion_blocked"
    assert rows["brand_crisis_response"]["state"] == "promotion_blocked"
    assert rows["seo_sprint"]["state"] == "promotion_blocked"
    assert "Required ABM connector" in " ".join(rows["abm_sprint"]["blocked_reasons"])
    assert "Required Brand connector" in " ".join(rows["brand_crisis_response"]["blocked_reasons"])
    assert "Required SEO connector" in " ".join(rows["seo_sprint"]["blocked_reasons"])
