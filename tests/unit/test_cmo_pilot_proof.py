from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import api.v1.kpis as kpis_api
from core.marketing.pilot_proof import (
    build_cmo_pilot_evidence_bundle,
    build_cmo_pilot_proof_projection,
    serialize_cmo_pilot_evidence_bundle,
)

NOW = datetime(2026, 5, 24, 15, 0, tzinfo=UTC)
FRESH_TS = (NOW - timedelta(hours=1)).isoformat()


def _connector_setup() -> list[dict[str, Any]]:
    return [
        {
            "key": "hubspot",
            "name": "HubSpot",
            "category": "CRM",
            "configured_status": "configured",
            "health_status": "healthy",
            "data_coverage_status": "ready",
            "cta_state": "none",
            "owner": "revops@example.com",
            "last_sync_at": FRESH_TS,
        },
        {
            "key": "google_ads",
            "name": "Google Ads",
            "category": "Paid Ads",
            "configured_status": "configured",
            "health_status": "healthy",
            "data_coverage_status": "ready",
            "cta_state": "none",
            "owner": "growth@example.com",
            "last_sync_at": FRESH_TS,
        },
    ]


def _connector_contracts(*, mock: bool = False, confirmed_write: bool = True) -> list[dict[str, Any]]:
    confirmation = []
    if confirmed_write:
        confirmation = [
            {
                "status": "write_confirmed",
                "action": "launch_campaign",
                "external_object_id": "customers/123/campaigns/456",
                "source_url": "https://ads.example/campaigns/456",
                "idempotency_key": "pilot-write-1",
                "audit_reference": "audit-write-1",
                "confirmed_at": FRESH_TS,
            }
        ]
    return [
        {
            "connector_key": "hubspot",
            "name": "HubSpot",
            "category": "CRM",
            "configured_status": "configured",
            "read_status": "ready",
            "write_status": "read_only",
            "read_ready": True,
            "write_ready": False,
            "write_safe": True,
            "production_ready": not mock,
            "mock_or_test_double": mock,
            "source_objects": [{"object": "deals", "source_url": "https://hubspot.example/deals"}],
            "external_write_confirmation_status": "none",
            "external_write_confirmations": [],
        },
        {
            "connector_key": "google_ads",
            "name": "Google Ads",
            "category": "Paid Ads",
            "configured_status": "configured",
            "read_status": "ready",
            "write_status": "ready",
            "read_ready": True,
            "write_ready": True,
            "write_safe": True,
            "production_ready": not mock,
            "mock_or_test_double": mock,
            "idempotency_key_supported": True,
            "source_objects": [{"object": "campaign", "source_url": "https://ads.example/campaigns/456"}],
            "external_write_confirmation_status": "write_confirmed" if confirmed_write else "none",
            "external_write_confirmations": confirmation,
        },
    ]


def _data_readiness(status: str = "ready") -> dict[str, Any]:
    return {
        "field_mapping_status": [
            {"connector_key": "hubspot", "status": "valid"},
            {"connector_key": "google_ads", "status": "valid"},
        ],
        "backfill_status": [
            {"connector_key": "hubspot", "status": "completed"},
            {"connector_key": "google_ads", "status": "completed"},
        ],
        "kpi_readiness": {
            "status": status,
            "field_mapping_readiness": "ready" if status == "ready" else "blocked",
            "backfill_readiness": "ready" if status == "ready" else "blocked",
            "next_action_cta": "none" if status == "ready" else "resolve_data_readiness",
        },
    }


def _workflow_activation(state: str = "active") -> dict[str, Any]:
    return {
        "workflow_activation_status": [
            {
                "workflow_key": "weekly_marketing_report",
                "state": state,
                "blocked_reasons": [] if state == "active" else ["missing connector"],
                "next_action_cta": "none" if state == "active" else "resolve_workflow_activation",
            },
            {
                "workflow_key": "campaign_launch",
                "state": state,
                "blocked_reasons": [] if state == "active" else ["missing connector"],
                "next_action_cta": "none" if state == "active" else "resolve_workflow_activation",
            },
        ],
        "workflow_activation_summary": {"readiness": "ready" if state == "active" else "blocked"},
    }


def _policy_projection(status: str = "ready") -> dict[str, Any]:
    return {
        "marketing_policy_summary": {
            "status": status,
            "policy_id": "cmo-default-policy",
            "version": "2026-05-24",
            "next_action_cta": "none" if status == "ready" else "configure_marketing_policy_manifest",
        }
    }


def _escalation_projection(status: str = "ready") -> dict[str, Any]:
    return {
        "marketing_escalation_summary": {
            "status": status,
            "policy_id": "cmo-default-escalation",
            "version": "2026-05-24",
            "next_action_cta": "none" if status == "ready" else "configure_escalation_matrix",
        }
    }


def _audit_projection(status: str = "ready") -> dict[str, Any]:
    return {
        "marketing_decision_audit_summary": {
            "status": status,
            "schema_version": "2026-05-23.cmo-6.3",
            "next_action_cta": "none" if status == "ready" else "configure_decision_audit_package",
        }
    }


def _kpi_schema() -> list[dict[str, Any]]:
    return [{"kpi_key": "cac"}, {"kpi_key": "roas"}, {"kpi_key": "pipeline_contribution"}]


def _kpi_results(status: str = "ready") -> list[dict[str, Any]]:
    return [
        {
            "kpi_key": "cac",
            "status": status,
            "value": 100,
            "source_refs": [{"connector_key": "google_ads", "object": "spend"}],
            "audit_refs": ["audit-kpi-cac"],
        },
        {
            "kpi_key": "roas",
            "status": status,
            "value": 2.1,
            "source_refs": [{"connector_key": "hubspot", "object": "deals"}],
            "audit_refs": ["audit-kpi-roas"],
        },
    ]


def _reconciliation(status: str = "passed") -> list[dict[str, Any]]:
    return [
        {
            "reconciliation_key": "paid_spend_totals_by_channel",
            "status": status,
            "severity": "high" if status in {"failed", "blocked"} else "info",
            "source_refs": [{"connector_key": "google_ads", "object": "campaigns"}],
        }
    ]


def _report_gates(status: str = "pass", *, deliverable: bool = True) -> list[dict[str, Any]]:
    return [
        {
            "report_key": "weekly_marketing_report",
            "status": status,
            "safe_report_mode": "deliverable" if deliverable else "draft_only",
            "trusted_delivery_allowed": deliverable,
            "audit_refs": ["audit-report-weekly"],
        }
    ]


def _drilldowns(status: str = "ready", *, production_lineage_ready: bool = True) -> list[dict[str, Any]]:
    return [
        {
            "kpi_key": "cac",
            "status": status,
            "production_lineage_ready": production_lineage_ready,
            "production_lineage_status": "ready" if production_lineage_ready else "blocked",
            "source_refs": [{"connector_key": "google_ads", "object": "campaign_spend"}],
        },
        {
            "kpi_key": "roas",
            "status": status,
            "production_lineage_ready": production_lineage_ready,
            "production_lineage_status": "ready" if production_lineage_ready else "blocked",
            "source_refs": [{"connector_key": "hubspot", "object": "attributed_revenue"}],
        },
    ]


def _approval_reviews(status: str = "approved") -> dict[str, Any]:
    return {
        "cmo_approval_reviews": [
            {
                "approval_id": "approval-pilot-1",
                "status": status,
                "audit_refs": ["audit-approval-1"],
                "allowed_reviewer_actions": [],
            }
        ],
        "cmo_approval_review_summary": {"total": 1, "approval_ready": 1 if status == "approved" else 0},
    }


def _lint_results(has_errors: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "workflow_file": "workflows/examples/cmo_campaign_launch.yaml",
            "workflow_id": "campaign_launch",
            "has_errors": has_errors,
            "errors": [{"code": "marketing_connector_missing"}] if has_errors else [],
            "warnings": [],
        }
    ]


def _test_evidence(kind: str) -> list[dict[str, Any]]:
    return [{"ref": f"tests/unit/test_cmo_{kind}.py", "scenario": kind, "status": "passed"}]


def _proof(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": "tenant-pilot",
        "company_id": "company-pilot",
        "environment_type": "real_vendor",
        "source_context": {"demo": False, "source": "computed"},
        "connector_setup": _connector_setup(),
        "connector_contracts": _connector_contracts(),
        "data_readiness": _data_readiness(),
        "workflow_activation": _workflow_activation(),
        "workflow_lint_results": _lint_results(),
        "policy_projection": _policy_projection(),
        "escalation_projection": _escalation_projection(),
        "approval_timeout_risk": {"pending": 0, "overdue": 0, "approval_timeout_decisions": []},
        "external_write_results": [],
        "decision_audit_projection": _audit_projection(),
        "kpi_schema": _kpi_schema(),
        "kpi_results": _kpi_results(),
        "reconciliation_checks": _reconciliation(),
        "report_quality_gates": _report_gates(),
        "work_queue": [],
        "kpi_drilldowns": _drilldowns(),
        "approval_review_projection": _approval_reviews(),
        "scenario_evidence": _test_evidence("e2e_scenarios"),
        "chaos_evidence": _test_evidence("chaos_failure_modes"),
        "now": NOW,
    }
    payload.update(overrides)
    return build_cmo_pilot_proof_projection(**payload)


def _proof_body(projection: dict[str, Any]) -> dict[str, Any]:
    return projection["cmo_pilot_proof"]


def test_demo_environment_returns_demo_only_not_production_passed() -> None:
    projection = _proof(environment_type="demo", source_context={"demo": True, "source": "demo"})
    proof = _proof_body(projection)

    assert proof["proof_status"] == "demo_only"
    assert proof["production_claim_allowed"] is False
    assert projection["cmo_pilot_proof_summary"]["next_action_cta"] == "connect_real_or_vendor_sandbox"


def test_test_double_environment_returns_test_only_not_production_passed() -> None:
    projection = _proof(environment_type="test_double", connector_contracts=_connector_contracts(mock=True))
    proof = _proof_body(projection)

    assert proof["proof_status"] == "test_only"
    assert proof["production_claim_allowed"] is False
    assert any(blocker["category"] == "environment" for blocker in proof["blockers"])


def test_missing_connectors_block_pilot_proof() -> None:
    projection = _proof(connector_setup=[], connector_contracts=[])
    proof = _proof_body(projection)

    assert proof["proof_status"] == "blocked"
    assert {"connector_setup", "connector_contracts"} <= {item["category"] for item in proof["blockers"]}


def test_missing_mapping_or_backfill_blocks_or_partials_pilot_proof() -> None:
    projection = _proof(environment_type="vendor_sandbox", data_readiness=_data_readiness("blocked"))
    proof = _proof_body(projection)

    assert proof["proof_status"] == "blocked"
    assert "data_readiness" in {item["category"] for item in proof["blockers"]}


def test_missing_policy_escalation_or_audit_readiness_blocks_proof() -> None:
    projection = _proof(
        policy_projection=_policy_projection("missing_policy"),
        escalation_projection=_escalation_projection("missing_route"),
        decision_audit_projection=_audit_projection("missing_audit_evidence"),
    )
    proof = _proof_body(projection)

    assert proof["proof_status"] == "blocked"
    assert {"policy", "escalation", "audit"} <= {item["category"] for item in proof["blockers"]}


def test_failed_reconciliation_or_report_gate_blocks_proof() -> None:
    projection = _proof(
        reconciliation_checks=_reconciliation("failed"),
        report_quality_gates=_report_gates("blocked", deliverable=False),
    )
    proof = _proof_body(projection)

    assert proof["proof_status"] == "blocked"
    assert {"kpi_reconciliation", "report_quality"} <= {item["category"] for item in proof["blockers"]}


def test_work_queue_critical_blockers_prevent_passed_status() -> None:
    projection = _proof(
        work_queue=[
            {
                "item_id": "work-critical-1",
                "category": "external_write",
                "severity": "critical",
                "status": "open",
                "next_action_key": "resolve_external_write_failure",
            }
        ]
    )
    proof = _proof_body(projection)

    assert proof["proof_status"] == "blocked"
    assert "work_queue" in {item["category"] for item in proof["blockers"]}


def test_vendor_sandbox_with_read_only_evidence_returns_partial() -> None:
    projection = _proof(
        environment_type="vendor_sandbox",
        connector_contracts=_connector_contracts(confirmed_write=False),
    )
    proof = _proof_body(projection)

    assert proof["proof_status"] == "partial"
    assert proof["environment_type"] == "vendor_sandbox"
    assert proof["real_vendor_claim_allowed"] is False
    assert any(risk["category"] in {"external_write", "sandbox_criteria"} for risk in proof["risks"])


def test_vendor_sandbox_with_confirmed_write_passes_only_sandbox_criteria() -> None:
    projection = _proof(environment_type="vendor_sandbox")
    proof = _proof_body(projection)

    assert proof["proof_status"] == "passed"
    assert proof["proof_scope"] == "sandbox"
    assert proof["production_claim_allowed"] is False
    assert proof["real_vendor_claim_allowed"] is False


def test_real_vendor_with_all_critical_criteria_returns_passed() -> None:
    projection = _proof()
    proof = _proof_body(projection)

    assert proof["proof_status"] == "passed"
    assert proof["environment_type"] == "real_vendor"
    assert proof["production_claim_allowed"] is True
    assert proof["readiness_score"] == 100


def test_beta_brand_social_abm_competitive_intel_and_seo_agents_are_unproven() -> None:
    projection = _proof()
    proof = _proof_body(projection)
    unproven = {item["capability_key"]: item for item in proof["unproven_capabilities"]}

    assert {
        "agent:brand_monitor",
        "agent:social_media",
        "agent:abm",
        "agent:competitive_intel",
        "agent:seo_strategist",
    } <= set(unproven)
    assert unproven["agent:brand_monitor"]["status"] == "beta"
    assert unproven["agent:social_media"]["status"] == "beta"
    assert unproven["agent:abm"]["status"] == "beta"
    assert unproven["agent:competitive_intel"]["status"] == "beta"
    assert unproven["agent:seo_strategist"]["status"] == "beta"
    assert proof["full_cmo_autonomy_claim_allowed"] is False


def test_pilot_evidence_bundle_serialization_redacts_secrets_and_tokens() -> None:
    projection = _proof()
    proof = _proof_body(projection)
    proof["evidence_refs"].append(
        {
            "type": "connector_secret",
            "ref_id": "secret-ref",
            "api_token": "live-token-value",
            "credentials_encrypted": {"password": "do-not-serialize"},
        }
    )
    bundle = build_cmo_pilot_evidence_bundle(proof)
    serialized = serialize_cmo_pilot_evidence_bundle(bundle)

    assert "live-token-value" not in serialized
    assert "do-not-serialize" not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.asyncio
async def test_kpis_cmo_response_exposes_pilot_proof_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_base(tenant_id: str, role: str, company_id: str) -> dict:
        return {
            "demo": False,
            "stale": False,
            "source": "computed",
            "environment_type": "vendor_sandbox",
            "company_id": company_id,
            "agent_count": 0,
            "total_tasks_30d": 0,
        }

    async def fake_configs(tenant_id: str) -> list:
        return []

    async def fake_approval_timeout(tenant_id: str, company_id: str) -> dict:
        return {"pending": 0, "overdue": 0, "approval_timeout_decisions": []}

    monkeypatch.setattr(kpis_api, "_build_kpi_response", fake_base)
    monkeypatch.setattr(kpis_api, "_load_marketing_connector_configs", fake_configs)
    monkeypatch.setattr(kpis_api, "_load_cmo_approval_timeout_risk", fake_approval_timeout)

    response = await kpis_api._build_cmo_kpi_response("tenant-1", "company-1")

    assert "cmo_pilot_proof" in response
    assert "cmo_pilot_proof_summary" in response
    assert response["cmo_pilot_proof_summary"]["environment_type"] == "vendor_sandbox"
    assert response["cmo_pilot_proof_summary"]["production_claim_allowed"] is False
