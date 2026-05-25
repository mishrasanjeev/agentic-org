from __future__ import annotations

from typing import Any

import pytest

from core.agents.marketing.seo_strategist import SeoStrategistAgent
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
        message_id=f"msg-seo-{action}",
        correlation_id="corr-seo",
        workflow_run_id="run-seo",
        workflow_definition_id="wf-seo",
        step_id=f"step-{action}",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(
            agent_id="agent-seo",
            agent_type="seo_strategist",
            agent_token="test-token",
        ),
        task=TaskInput(action=action, inputs=inputs),
    )


def _agent(gateway: FakeToolGateway | None = None) -> SeoStrategistAgent:
    return SeoStrategistAgent(
        agent_id="agent-seo",
        tenant_id="tenant-cmo",
        tool_gateway=gateway,
        authorized_tools=["wordpress.apply_seo_site_change", "google_search_console.submit_url_to_index"],
    )


def _seo_inputs(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "owned_keywords": [
            {"keyword": "marketing automation", "rank": 18, "volume": 1600, "difficulty": 62},
            {"keyword": "campaign roi", "rank": 5, "volume": 900, "difficulty": 44},
        ],
        "competitor_keywords": [
            {
                "keyword": "autonomous marketing agents",
                "rank": 2,
                "volume": 2600,
                "difficulty": 45,
                "intent": "commercial",
            },
            {"keyword": "ai cmo platform", "rank": 3, "volume": 2200, "difficulty": 48, "intent": "commercial"},
            {"keyword": "marketing automation", "rank": 6, "volume": 1600, "difficulty": 62},
        ],
        "rankings": [
            {"keyword": "marketing automation", "rank": 18, "url": "https://example.com/automation"},
            {"keyword": "campaign roi", "rank": 5, "url": "https://example.com/roi"},
            {"keyword": "ai cmo platform", "rank": 14, "url": "https://example.com/cmo"},
        ],
        "previous_rankings": [
            {"keyword": "marketing automation", "rank": 23, "url": "https://example.com/automation"},
            {"keyword": "campaign roi", "rank": 3, "url": "https://example.com/roi"},
            {"keyword": "lost seo term", "rank": 9, "url": "https://example.com/lost"},
        ],
        "technical_issues": [
            {
                "id": "issue-cwv",
                "type": "core_web_vitals",
                "severity": "critical",
                "impact": 90,
                "effort": "medium",
                "affected_pages": 14,
            },
            {
                "id": "issue-meta",
                "type": "metadata",
                "severity": "medium",
                "impact": 70,
                "effort": "low",
                "affected_pages": 40,
            },
        ],
        "pages": [
            {
                "url": "https://example.com/cmo",
                "title": "CMO operating system",
                "meta_description": "Run marketing operations with agents.",
                "headings": ["Marketing command center"],
                "word_count": 650,
                "internal_links": [],
                "target_keyword": "ai cmo platform",
            }
        ],
        "source_refs": [{"type": "ahrefs", "ref_id": "project-123"}],
        "workflow_mode": "shadow",
    }
    payload.update(overrides)
    return payload


def _write_contract(**overrides: object) -> dict[str, Any]:
    contract: dict[str, Any] = {
        "connector_key": "wordpress",
        "category": "CMS",
        "read_ready": True,
        "write_ready": True,
        "write_safe": True,
        "idempotency_key_supported": True,
        "retry_budget": {
            "idempotency_key": "seo-write-001",
            "idempotency_supported": True,
            "remaining_attempts": 2,
        },
    }
    contract.update(overrides)
    return contract


def _workflow(step: dict[str, Any], *, mode: str = "production") -> dict[str, Any]:
    return {
        "id": "wf_seo",
        "name": "SEO Sprint",
        "domain": "marketing",
        "mode": mode,
        "steps": [step],
    }


def _workflow_step(action: str, **overrides: object) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "step_seo",
        "name": "SEO Step",
        "type": "agent",
        "agent_type": "seo_strategist",
        "action": action,
    }
    step.update(overrides)
    return step


@pytest.mark.asyncio
async def test_keyword_gap_analysis_identifies_missing_and_underperforming_opportunities() -> None:
    result = await _agent().execute(_assignment("keyword_gap_analysis", _seo_inputs()))
    gaps = result.output["keyword_gaps"]

    assert result.output["status"] == "keyword_gaps_identified"
    assert gaps[0]["keyword"] == "autonomous marketing agents"
    assert gaps[0]["reason"] == "missing"
    assert any(gap["keyword"] == "marketing automation" and gap["reason"] == "underperforming" for gap in gaps)


@pytest.mark.asyncio
async def test_ranking_delta_computation_handles_improvements_drops_new_and_lost() -> None:
    result = await _agent().execute(_assignment("ranking_delta_computation", _seo_inputs()))
    deltas = {row["keyword"]: row for row in result.output["ranking_deltas"]}

    assert deltas["marketing automation"]["status"] == "improved"
    assert deltas["campaign roi"]["status"] == "dropped"
    assert deltas["ai cmo platform"]["status"] == "new"
    assert deltas["lost seo term"]["status"] == "lost"


@pytest.mark.asyncio
async def test_technical_issue_prioritization_orders_by_severity_impact_and_effort() -> None:
    result = await _agent().execute(_assignment("technical_issue_prioritization", _seo_inputs()))
    issues = result.output["technical_issues"]

    assert issues[0]["issue_id"] == "issue-cwv"
    assert issues[0]["recommended_action"] == "improve_page_performance"
    assert issues[0]["priority_score"] > issues[1]["priority_score"]


@pytest.mark.asyncio
async def test_recommendations_bundle_by_effort_and_impact() -> None:
    result = await _agent().execute(_assignment("recommendation_bundling", _seo_inputs()))
    bundles = result.output["recommendation_bundles"]

    assert bundles["quick_wins"]
    assert bundles["strategic"]
    assert all("priority_score" in item for rows in bundles.values() for item in rows)


@pytest.mark.asyncio
async def test_content_optimization_recommendation_is_deterministic() -> None:
    result = await _agent().execute(_assignment("content_optimization_recommendation", _seo_inputs()))
    recommendation = result.output["content_recommendations"][0]

    assert recommendation["url"] == "https://example.com/cmo"
    assert "add_target_keyword_to_title" in recommendation["actions"]
    assert "refresh_meta_description_with_target_keyword" in recommendation["actions"]
    assert "add_internal_links" in recommendation["actions"]


@pytest.mark.asyncio
async def test_seo_sprint_plan_includes_prioritized_actions_and_expected_impact() -> None:
    result = await _agent().execute(_assignment("seo_sprint_planning", _seo_inputs(sprint_capacity=4)))
    sprint = result.output["sprint_plan"]

    assert result.output["status"] == "seo_sprint_planned"
    assert len(sprint["actions"]) == 4
    assert sprint["expected_impact"]["technical_fixes"] >= 1
    assert sprint["requires_approval_for_writes"] is True


@pytest.mark.asyncio
async def test_stale_or_partial_connector_data_downgrades_confidence_and_status() -> None:
    result = await _agent().execute(
        _assignment(
            "seo_sprint_planning",
            _seo_inputs(connector_read_ready=False, stale_data=True, partial_data=True),
        )
    )

    assert result.output["status"] == "seo_sprint_degraded"
    assert result.output["degraded_reasons"]
    assert result.output["confidence"] < 0.84


@pytest.mark.asyncio
async def test_technical_site_change_requires_policy_approval_audit_and_write_readiness() -> None:
    result = await _agent().execute(
        _assignment(
            "update_page_metadata",
            _seo_inputs(
                workflow_mode="active",
                connector_contract=_write_contract(),
                url="https://example.com/cmo",
                title="AI CMO platform",
                meta_description="AI CMO operating system for governed marketing.",
            ),
        )
    )

    assert result.status == "hitl_triggered"
    assert result.output["status"] == "blocked"
    assert result.output["policy_result"]["decision"] == "requires_approval"
    assert result.output["approval_required"] is True
    assert result.output["audit_ref"].startswith("seo_strategist:")
    assert result.output["external_write_confirmation_status"] == "write_unconfirmed"


@pytest.mark.asyncio
async def test_connector_degraded_or_missing_state_downgrades_or_blocks_appropriately() -> None:
    read_result = await _agent().execute(
        _assignment("identify_content_gaps", _seo_inputs(connector_read_ready=False))
    )
    write_result = await _agent().execute(
        _assignment(
            "update_page_metadata",
            _seo_inputs(
                workflow_mode="active",
                approved=True,
                connector_contract=_write_contract(write_ready=False, write_safe=False),
            ),
        )
    )

    assert read_result.output["status"] == "keyword_gaps_degraded"
    assert read_result.output["degraded_reasons"]
    assert write_result.output["status"] == "blocked"
    assert any("not write-safe" in reason for reason in write_result.output["blocked_reasons"])


@pytest.mark.asyncio
async def test_shadow_read_only_advisory_mode_cannot_write_externally() -> None:
    gateway = FakeToolGateway({})
    result = await _agent(gateway).execute(
        _assignment("update_page_metadata", _seo_inputs(workflow_mode="shadow", title="Draft SEO title"))
    )

    assert result.output["status"] == "shadow_only"
    assert result.output["external_write_confirmation_status"] == "shadow_only"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_active_external_site_write_cannot_complete_without_confirmed_write() -> None:
    result = await _agent().execute(
        _assignment(
            "update_page_metadata",
            _seo_inputs(
                workflow_mode="active",
                approved=True,
                connector_contract=_write_contract(),
                external_write_result={
                    "connector_key": "wordpress",
                    "status": "accepted",
                    "idempotency_key": "seo:metadata:1",
                },
            ),
        )
    )

    assert result.output["status"] == "write_unconfirmed"
    assert result.output["external_writes_completed"] is False
    assert result.status == "hitl_triggered"


@pytest.mark.asyncio
async def test_confirmed_site_write_has_policy_audit_escalation_refs_when_required() -> None:
    result = await _agent().execute(
        _assignment(
            "update_page_metadata",
            _seo_inputs(
                workflow_mode="active",
                approved=True,
                legal_claim=True,
                connector_contract=_write_contract(),
                external_write_result={
                    "connector_key": "wordpress",
                    "status": "write_confirmed",
                    "external_object_id": "post-123:metadata",
                    "source_url": "https://cms.example/post-123",
                    "idempotency_key": "seo:metadata:1",
                    "confirmed_at": "2026-05-24T12:00:00Z",
                    "audit_ref": "audit-seo-write",
                },
            ),
        )
    )
    contract = build_marketing_agent_contract_output(
        "seo_strategist",
        "update_page_metadata",
        result=result,
        audit_ref=result.output["audit_ref"],
        external_write_confirmation_status=result.output["external_write_confirmation_status"],
    )

    assert result.status == "completed"
    assert result.output["policy_result"]["decision"] == "requires_escalation"
    assert result.output["escalation_ref"]
    assert result.output["external_write_ref"]["external_object_id"] == "post-123:metadata"
    assert contract_has_required_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["external_writes_completed"] is True


def test_workflow_linter_activation_and_pilot_proof_are_truthful_beta() -> None:
    read_lint = lint_marketing_workflow(_workflow(_workflow_step("keyword_gap_analysis"), mode="target"))
    write_lint = lint_marketing_workflow(
        _workflow(_workflow_step("update_page_metadata", connector_key="wordpress")),
        connector_contracts=[_write_contract()],
    )
    proof = build_cmo_pilot_proof_projection(
        environment_type="vendor_sandbox",
        source_context={"source": "vendor_sandbox"},
        agent_contracts=[get_marketing_agent_contract_spec("seo_strategist")],
    )["cmo_pilot_proof"]
    contract = build_marketing_agent_contract_output(
        "seo_strategist",
        "keyword_gap_analysis",
        audit_ref="audit-seo-contract",
    )

    assert contract_has_required_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False
    assert "marketing_agent_type_unknown" not in {finding.code for finding in read_lint.findings}
    assert "marketing_agent_stub_target_only" not in {finding.code for finding in read_lint.findings}
    assert "marketing_agent_beta_in_production" in {finding.code for finding in write_lint.findings}
    assert "marketing_external_write_confirmation_metadata_missing" in {
        finding.code for finding in write_lint.findings
    }
    assert proof["production_claim_allowed"] is False
    assert any(item["capability_key"] == "agent:seo_strategist" for item in proof["unproven_capabilities"])
