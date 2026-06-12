from __future__ import annotations

from typing import Any

import pytest

from core.agents.marketing.brand_monitor import BrandMonitorAgent
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
        message_id=f"msg-brand-{action}",
        correlation_id="corr-brand",
        workflow_run_id="run-brand",
        workflow_definition_id="wf-brand",
        step_id=f"step-{action}",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(
            agent_id="agent-brand",
            agent_type="brand_monitor",
            agent_token="test-token",
        ),
        task=TaskInput(action=action, inputs=inputs),
    )


def _agent(gateway: FakeToolGateway | None = None) -> BrandMonitorAgent:
    return BrandMonitorAgent(
        agent_id="agent-brand",
        tenant_id="tenant-cmo",
        tool_gateway=gateway,
        authorized_tools=["buffer.publish_brand_response", "brandwatch.record_brand_claim"],
    )


def _mentions() -> list[dict[str, Any]]:
    return [
        {
            "id": "m1",
            "source": "twitter",
            "channel": "social",
            "topic": "support",
            "text": "AgenticOrg support resolved our issue quickly. Great team.",
            "sentiment": "positive",
            "url": "https://x.example/m1",
        },
        {
            "id": "m2",
            "source": "twitter",
            "channel": "social",
            "topic": "reliability",
            "text": "AgenticOrg is down again and the outage is unacceptable.",
            "sentiment": "negative",
            "url": "https://x.example/m2",
        },
        {
            "id": "m3",
            "source": "news",
            "channel": "news",
            "topic": "legal",
            "text": "Regulator asks questions after AgenticOrg security incident.",
            "sentiment": "negative",
            "influence": "journalist",
            "url": "https://news.example/m3",
        },
        {
            "id": "m4",
            "source": "reddit",
            "channel": "community",
            "topic": "general",
            "text": "Bought a brand new monitor for my desk.",
            "sentiment": "neutral",
        },
        {
            "id": "m5",
            "source": "brandwatch",
            "channel": "social",
            "topic": "competitor",
            "entity_type": "competitor",
            "competitor": "AcmeOps",
            "text": "AcmeOps says AgenticOrg lacks governance features.",
            "sentiment": "negative",
        },
    ]


def _base_inputs(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mentions": _mentions(),
        "baseline_negative_mentions": 1,
        "negative_spike_multiplier": 2,
        "negative_spike_min_count": 2,
        "source_refs": [{"type": "brandwatch", "ref_id": "query-brand"}],
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
            "idempotency_key": "brand:write:001",
            "idempotency_supported": True,
            "remaining_attempts": 2,
        },
    }
    contract.update(overrides)
    return contract


def _workflow(step: dict[str, Any], *, mode: str = "production") -> dict[str, Any]:
    return {
        "id": "wf_brand",
        "name": "Brand Crisis Response",
        "domain": "marketing",
        "mode": mode,
        "steps": [step],
    }


def _workflow_step(action: str, **overrides: object) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "step_brand",
        "name": "Brand Step",
        "type": "agent",
        "agent_type": "brand_monitor",
        "action": action,
    }
    step.update(overrides)
    return step


@pytest.mark.asyncio
async def test_brand_monitor_mention_aggregation_groups_mentions_by_source_channel_and_topic() -> None:
    result = await _agent().execute(_assignment("mention_aggregation", _base_inputs()))
    groups = result.output["brand_monitoring"]["mention_groups"]

    assert result.output["status"] == "mentions_aggregated"
    assert groups["by_source"]["twitter"] == 2
    assert groups["by_channel"]["social"] == 3
    assert groups["by_topic"]["reliability"] == 1
    assert result.output["brand_monitoring"]["suppressed_mentions"] == 1


@pytest.mark.asyncio
async def test_brand_monitor_sentiment_trend_calculates_distribution() -> None:
    result = await _agent().execute(_assignment("sentiment_trend", _base_inputs()))
    trend = result.output["sentiment_trend"]

    assert trend["counts"] == {"positive": 1, "neutral": 0, "negative": 3}
    assert trend["distribution"]["negative"] == 0.75
    assert trend["baseline_negative_mentions"] == 1


@pytest.mark.asyncio
async def test_brand_monitor_negative_spike_detection_triggers_alert_above_threshold() -> None:
    result = await _agent().execute(_assignment("detect_negative_spike", _base_inputs()))
    spike = result.output["negative_spike"]

    assert spike["triggered"] is True
    assert spike["current_negative_mentions"] == 3
    assert spike["severity"] in {"medium", "high", "critical"}


@pytest.mark.asyncio
async def test_brand_monitor_false_positive_suppression_reduces_noise() -> None:
    result = await _agent().execute(_assignment("false_positive_suppression", _base_inputs()))

    assert len(result.output["mentions"]) == 4
    assert len(result.output["suppressed_mentions"]) == 1
    assert "brand new monitor" in result.output["suppressed_mentions"][0]["text"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mentions", "expected"),
    [
        ([{"text": "AgenticOrg is great", "sentiment": "positive"}], "low"),
        (
            [
                {"text": "AgenticOrg support is bad", "sentiment": "negative"},
                {"text": "AgenticOrg was slow", "sentiment": "negative"},
                {"text": "AgenticOrg issue unresolved", "sentiment": "negative"},
            ],
            "medium",
        ),
        (
            [{"text": "AgenticOrg outage is spreading and users are angry", "sentiment": "negative"}] * 8,
            "high",
        ),
        (
            [
                {
                    "text": "AgenticOrg security breach and regulator lawsuit after data leak",
                    "sentiment": "negative",
                    "influence": "journalist",
                }
            ],
            "critical",
        ),
    ],
)
async def test_brand_monitor_crisis_severity_classification_returns_expected_levels(
    mentions: list[dict[str, Any]],
    expected: str,
) -> None:
    result = await _agent().execute(
        _assignment(
            "classify_crisis_severity",
            _base_inputs(mentions=mentions, baseline_negative_mentions=1, negative_spike_min_count=3),
        )
    )

    assert result.output["crisis_severity"]["severity"] == expected


@pytest.mark.asyncio
async def test_brand_monitor_competitor_and_brand_grouping_works_from_structured_input() -> None:
    result = await _agent().execute(_assignment("competitor_brand_grouping", _base_inputs()))
    groups = result.output["mention_groups"]

    assert groups["by_entity"]["brand"] == 3
    assert groups["by_entity"]["competitor"] == 1
    assert groups["competitors"]["acmeops"] == 1


@pytest.mark.asyncio
async def test_brand_monitor_response_playbook_is_deterministic() -> None:
    result = await _agent().execute(_assignment("recommend_response_playbook", _base_inputs()))
    actions = [item["action"] for item in result.output["response_playbook"]]

    assert actions[0] == "escalate_brand_crisis"
    assert "prepare_approved_holding_statement" in actions


@pytest.mark.asyncio
async def test_public_crisis_response_requires_policy_approval_and_escalation() -> None:
    result = await _agent().execute(
        _assignment(
            "public_response",
            _base_inputs(
                workflow_mode="active",
                connector_contract=_write_contract(),
                severity="critical",
                crisis_response=True,
                message="We are investigating the reported security incident.",
            ),
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "blocked"
    assert result.output["policy_result"]["decision"] == "requires_escalation"
    assert result.output["approval_required"] is True
    assert result.output["escalation_ref"]


@pytest.mark.asyncio
async def test_connector_degraded_read_only_monitoring_downgrades_output() -> None:
    result = await _agent().execute(
        _assignment(
            "monitor_mentions",
            _base_inputs(connector_read_ready=False, connector_degraded=True),
        )
    )

    assert result.output["status"] == "mentions_degraded"
    assert result.output["degraded_reasons"]
    assert result.output["confidence"] < 0.85


@pytest.mark.asyncio
async def test_shadow_mode_cannot_write_externally() -> None:
    gateway = FakeToolGateway({})
    result = await _agent(gateway).execute(
        _assignment("public_response", _base_inputs(message="Draft public response", workflow_mode="shadow"))
    )

    assert result.output["status"] == "shadow_only"
    assert result.output["external_write_confirmation_status"] == "shadow_only"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_active_external_action_blocks_when_connector_is_not_write_safe() -> None:
    result = await _agent().execute(
        _assignment(
            "public_response",
            _base_inputs(
                workflow_mode="active",
                approved=True,
                escalation_ref="esc-brand-1",
                connector_contract=_write_contract(write_ready=False, write_safe=False),
            ),
        )
    )

    assert result.output["status"] == "blocked"
    assert any("not write-safe" in reason for reason in result.output["blocked_reasons"])


@pytest.mark.asyncio
async def test_active_external_action_cannot_complete_without_confirmed_write() -> None:
    result = await _agent().execute(
        _assignment(
            "public_response",
            _base_inputs(
                workflow_mode="active",
                approved=True,
                escalation_ref="esc-brand-1",
                connector_contract=_write_contract(),
                external_write_result={
                    "connector_key": "buffer",
                    "status": "accepted",
                    "idempotency_key": "brand:response:1",
                },
            ),
        )
    )

    assert result.output["status"] == "write_unconfirmed"
    assert result.output["external_writes_completed"] is False
    assert result.status == "hitl_triggered"


@pytest.mark.asyncio
async def test_confirmed_brand_response_write_has_refs_and_contract_shape() -> None:
    result = await _agent().execute(
        _assignment(
            "publish_brand_response",
            _base_inputs(
                workflow_mode="active",
                approved=True,
                escalation_ref="esc-brand-1",
                connector_contract=_write_contract(),
                external_write_result={
                    "connector_key": "buffer",
                    "status": "write_confirmed",
                    "external_object_id": "updates/brand-response-1",
                    "source_url": "https://publish.buffer.com/updates/brand-response-1",
                    "idempotency_key": "brand:response:1",
                    "confirmed_at": "2026-05-24T12:00:00Z",
                    "audit_ref": "audit-brand-write",
                },
            ),
        )
    )
    contract = build_marketing_agent_contract_output(
        "brand_monitor",
        "publish_brand_response",
        result=result,
        audit_ref=result.output["audit_ref"],
        external_write_confirmation_status=result.output["external_write_confirmation_status"],
    )

    assert result.status == "completed"
    assert result.output["external_write_ref"]["external_object_id"] == "updates/brand-response-1"
    assert contract_has_required_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["external_writes_completed"] is True


def test_brand_monitor_contract_linter_and_pilot_proof_are_truthful_beta() -> None:
    contract = build_marketing_agent_contract_output(
        "brand_monitor",
        "monitor_mentions",
        audit_ref="audit-brand-contract",
    )
    read_lint = lint_marketing_workflow(_workflow(_workflow_step("monitor_mentions")))
    write_lint = lint_marketing_workflow(
        _workflow(_workflow_step("public_response", connector_key="buffer")),
        connector_contracts=[_write_contract()],
    )
    proof = build_cmo_pilot_proof_projection(
        environment_type="vendor_sandbox",
        source_context={"source": "vendor_sandbox"},
        agent_contracts=[get_marketing_agent_contract_spec("brand_monitor")],
    )["cmo_pilot_proof"]

    assert contract_has_required_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False
    assert "marketing_agent_stub_for_production" not in {finding.code for finding in read_lint.findings}
    assert "marketing_agent_beta_in_production" in {finding.code for finding in read_lint.findings}
    assert "marketing_external_write_confirmation_metadata_missing" in {
        finding.code for finding in write_lint.findings
    }
    assert proof["production_claim_allowed"] is False
    assert any(item["capability_key"] == "agent:brand_monitor" for item in proof["unproven_capabilities"])
