from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.v1 import kpis as kpis_api
from core.marketing.work_queue import build_cmo_work_queue_projection

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def _projection(**kwargs: object) -> dict:
    return build_cmo_work_queue_projection(now=NOW, **kwargs)


def _first(projection: dict, *, item_type: str | None = None, category: str | None = None) -> dict:
    rows = projection["cmo_work_queue"]
    for row in rows:
        if item_type and row["type"] != item_type:
            continue
        if category and row["category"] != category:
            continue
        return row
    raise AssertionError(f"work item not found: {item_type=} {category=}")


def test_connector_auth_expired_creates_high_priority_reconnect_item() -> None:
    projection = _projection(
        connector_setup=[
            {
                "key": "ga4",
                "name": "Google Analytics 4",
                "category": "Analytics",
                "configured_status": "configured",
                "health_status": "expired_auth",
                "owner": "marketing_ops",
                "cta_state": "reconnect",
            }
        ]
    )

    item = _first(projection, category="connector")

    assert item["severity"] in {"high", "critical"}
    assert item["affected_connector"] == "ga4"
    assert item["next_action_key"] == "reconnect"
    assert item["next_action_label"] == "Reconnect"


def test_missing_mapping_and_backfill_create_setup_work_items() -> None:
    projection = _projection(
        data_readiness={
            "field_mapping_status": [
                {
                    "key": "lifecycle_stages",
                    "name": "Lifecycle stages",
                    "status": "unmapped",
                    "next_action_cta": "map_fields",
                }
            ],
            "backfill_status": [
                {
                    "source_connector_key": "hubspot",
                    "source_name": "HubSpot",
                    "category": "CRM",
                    "status": "failed",
                    "blocking_reason": "Vendor export failed",
                    "next_action_cta": "retry_backfill",
                }
            ],
        }
    )

    mapping = _first(projection, item_type="mapping_blocker")
    backfill = _first(projection, item_type="backfill_blocker")

    assert mapping["next_action_key"] == "map_fields"
    assert mapping["status"] == "blocked"
    assert backfill["next_action_key"] == "retry_backfill"
    assert "Vendor export failed" in backfill["message"]


def test_workflow_activation_blocker_creates_promotion_readiness_item() -> None:
    projection = _projection(
        workflow_activation={
            "workflow_activation_status": [
                {
                    "workflow_key": "campaign_launch",
                    "name": "Campaign Launch",
                    "state": "promotion_blocked",
                    "blocked_reasons": ["Required Ads connector is not healthy (missing)."],
                    "next_action_cta": "fix_required_connector",
                }
            ]
        }
    )

    item = _first(projection, item_type="workflow_activation")

    assert item["affected_workflow"] == "campaign_launch"
    assert item["severity"] == "high"
    assert item["next_action_key"] == "fix_required_connector"


def test_overdue_approval_timeout_creates_critical_escalation_item() -> None:
    projection = _projection(
        approval_timeout_risk={
            "approval_timeout_decisions": [
                {
                    "approval_id": "apr-1",
                    "approval_type": "ad_campaign_launch",
                    "status": "timed_out",
                    "timed_out": True,
                    "created_at": (NOW - timedelta(hours=6)).isoformat(),
                    "due_at": (NOW - timedelta(hours=2)).isoformat(),
                    "external_writes_allowed": False,
                    "safe_fallback_message": "Campaign launch was cancelled because approval expired.",
                    "next_action_cta": "resolve_overdue_approvals",
                    "audit_reference": "mkt_approval_timeout_1",
                    "escalation_decision": {
                        "event_id": "esc-1",
                        "trigger_type": "approval_timeout",
                        "decision": "escalate",
                        "severity": "high",
                        "escalation_target": "cmo",
                        "due_at": (NOW + timedelta(hours=1)).isoformat(),
                        "next_action_cta": "review_escalation",
                    },
                }
            ]
        }
    )

    approval = _first(projection, item_type="approval_timeout")
    escalation = _first(projection, category="escalation")

    assert approval["severity"] == "critical"
    assert approval["status"] == "blocked"
    assert approval["next_action_key"] == "resolve_overdue_approvals"
    assert escalation["owner_role"] == "cmo"


def test_external_write_rejected_and_timeout_create_critical_items() -> None:
    projection = _projection(
        external_write_results=[
            {
                "workflow_id": "campaign_launch",
                "step_id": "launch_ads",
                "connector_key": "google_ads",
                "final_state": "rejected",
                "reason": "Budget cap exceeded",
                "next_action": "request_budget_approval",
                "audit_reference": "mkt_write_rejected",
            },
            {
                "workflow_id": "lead_nurture",
                "step_id": "send_email",
                "connector_key": "mailchimp",
                "final_state": "timeout_unknown",
                "reason": "Unknown vendor write state",
                "next_action": "manual_reconcile_before_retry",
            },
        ]
    )

    items = [item for item in projection["cmo_work_queue"] if item["type"] == "external_write_failure"]

    assert {item["affected_connector"] for item in items} == {"google_ads", "mailchimp"}
    assert all(item["severity"] == "critical" for item in items)
    assert all(item["status"] == "blocked" for item in items)


def test_missing_policy_and_audit_evidence_create_blocked_readiness_items() -> None:
    projection = _projection(
        workflow_activation={
            "workflow_activation_status": [
                {
                    "workflow_key": "content_pipeline",
                    "name": "Content Pipeline",
                    "state": "promotion_blocked",
                    "blocked_reasons": ["Decision audit evidence is missing for publish."],
                    "next_action_cta": "configure_decision_audit_package",
                    "marketing_policy": {
                        "status": "missing_policy",
                        "missing_policy_actions": ["publish"],
                        "next_action_cta": "configure_marketing_policy_manifest",
                    },
                    "decision_audit": {
                        "status": "missing_audit_evidence",
                        "missing_audit_actions": ["publish"],
                        "next_action_cta": "configure_decision_audit_package",
                    },
                }
            ]
        }
    )

    policy = _first(projection, category="policy")
    audit = _first(projection, category="audit")

    assert policy["status"] == "blocked"
    assert policy["next_action_key"] == "configure_marketing_policy_manifest"
    assert audit["status"] == "blocked"
    assert audit["next_action_key"] == "configure_decision_audit_package"


def test_blocked_kpi_and_failed_reconciliation_create_work_items() -> None:
    projection = _projection(
        kpi_results=[
            {
                "kpi_key": "cac",
                "status": "blocked",
                "confidence": 0.0,
                "missing_requirements": {"connectors": ["Ads"]},
                "next_action_cta": "review_kpi_readiness",
            }
        ],
        reconciliation_checks=[
            {
                "reconciliation_key": "paid_spend_totals_by_channel",
                "status": "failed",
                "severity": "high",
                "affected_kpi_keys": ["cac", "roas"],
                "next_action_cta": "resolve_reconciliation",
                "decision_audit_ref": "cmo_decision_audit:spend",
            }
        ],
    )

    kpi = _first(projection, category="kpi")
    reconciliation = _first(projection, category="reconciliation")

    assert kpi["affected_kpi"] == "cac"
    assert kpi["status"] == "blocked"
    assert reconciliation["severity"] == "high"
    assert "cmo_decision_audit:spend" in reconciliation["audit_refs"]


def test_report_quality_blocked_and_warning_modes_create_prioritized_items() -> None:
    projection = _projection(
        report_quality_gates=[
            {
                "report_key": "weekly_marketing_report",
                "display_name": "Weekly Marketing Report",
                "status": "blocked",
                "severity": "high",
                "safe_report_mode": "draft_only",
                "blocked_reasons": ["Required KPI cac is blocked."],
                "next_action_cta": "restore_required_kpis",
            },
            {
                "report_key": "campaign_performance_ad_hoc",
                "display_name": "Campaign Ad Hoc",
                "status": "warning",
                "safe_report_mode": "internal_only",
                "warning_reasons": ["Optional content data is stale."],
                "next_action_cta": "review_report_quality_warnings",
            },
        ]
    )

    report_items = [item for item in projection["cmo_work_queue"] if item["category"] == "report"]

    assert report_items[0]["affected_report"] == "weekly_marketing_report"
    assert report_items[0]["severity"] == "high"
    assert report_items[1]["affected_report"] == "campaign_performance_ad_hoc"
    assert report_items[1]["severity"] == "medium"


def test_prioritization_orders_critical_before_lower_severity_items() -> None:
    projection = _projection(
        report_quality_gates=[
            {
                "report_key": "campaign_performance_ad_hoc",
                "display_name": "Campaign Ad Hoc",
                "status": "warning",
                "safe_report_mode": "internal_only",
                "warning_reasons": ["Optional source is stale."],
            }
        ],
        external_write_results=[
            {
                "workflow_id": "campaign_launch",
                "step_id": "launch_ads",
                "connector_key": "google_ads",
                "final_state": "timeout_unknown",
                "reason": "Unknown vendor write state",
            }
        ],
    )

    assert projection["cmo_work_queue"][0]["category"] == "external_write"
    assert projection["cmo_work_queue"][0]["severity"] == "critical"
    assert projection["cmo_work_queue"][1]["category"] == "report"


def test_related_connector_items_are_deduped_and_grouped() -> None:
    projection = _projection(
        connector_setup=[
            {
                "key": "ga4",
                "name": "Google Analytics 4",
                "category": "Analytics",
                "configured_status": "configured",
                "health_status": "expired_auth",
                "owner": "marketing_ops",
                "cta_state": "reconnect",
            }
        ],
        connector_contracts=[
            {
                "connector_key": "ga4",
                "name": "Google Analytics 4",
                "category": "Analytics",
                "contract_state": "auth_expired",
                "read_status": "blocked",
                "write_status": "blocked",
                "write_capabilities": [],
                "next_action_cta": "reconnect",
                "degraded_mode_reason": "Connector auth is expired.",
            }
        ],
    )

    connector_items = [item for item in projection["cmo_work_queue"] if item["category"] == "connector"]

    assert len(connector_items) == 1
    assert len(connector_items[0]["source_refs"]) == 2
    assert connector_items[0]["next_action_key"] == "reconnect"


@pytest.mark.asyncio
async def test_kpis_cmo_response_exposes_work_queue_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_base(tenant_id: str, role: str, company_id: str) -> dict:
        return {
            "demo": False,
            "stale": False,
            "source": "computed",
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

    assert "cmo_work_queue" in response
    assert "cmo_work_queue_summary" in response
    assert response["cmo_work_queue_summary"]["total"] > 0
    assert response["cmo_work_queue_summary"]["readiness"] == "blocked"


def test_empty_work_queue_state_is_clear_and_non_deceptive() -> None:
    projection = _projection()

    assert projection["cmo_work_queue"] == []
    assert projection["cmo_work_queue_summary"]["readiness"] == "ready"
    assert "does not upgrade stub" in projection["cmo_work_queue_summary"]["empty_state"]
