from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from core.marketing.agent_contracts import (
    build_marketing_agent_contract_output,
    contract_has_required_shape,
)
from core.marketing.approval_review import build_cmo_approval_review_projection
from core.marketing.approval_timeouts import build_approval_timeout_risk
from core.marketing.connector_contracts import build_marketing_connector_contracts
from core.marketing.connector_setup import build_marketing_connector_setup
from core.marketing.data_readiness import build_marketing_data_readiness
from core.marketing.decision_audit import (
    build_cmo_decision_audit_package,
    build_marketing_decision_audit_projection,
)
from core.marketing.escalation_matrix import (
    build_marketing_escalation_projection,
    evaluate_marketing_escalation,
)
from core.marketing.external_writes import evaluate_marketing_external_write_result
from core.marketing.kpi_drilldown import build_cmo_kpi_drilldown_projection
from core.marketing.kpi_schema import build_unified_cmo_kpi_projection
from core.marketing.policy_manifest import (
    build_marketing_policy_projection,
    evaluate_marketing_policy,
)
from core.marketing.report_quality import build_cmo_report_quality_projection
from core.marketing.work_queue import build_cmo_work_queue_projection
from core.marketing.workflow_activation import build_cmo_workflow_activation
from core.marketing.workflow_linter import lint_marketing_workflow

NOW = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
FRESH_TS = (NOW - timedelta(hours=1)).isoformat()
MAPPING_TS = "2026-05-01T00:00:00+00:00"
IDEMPOTENCY_KEY = "mkt-e2e-idempotency-001"


def _connector_config(
    connector_name: str,
    *,
    config: dict[str, Any],
    health_status: str = "healthy",
    last_sync_at: datetime | None = None,
    sync_error: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        connector_name=connector_name,
        display_name=None,
        auth_type="oauth2",
        credentials_encrypted={"_encrypted": "enc"},
        config=config,
        status="configured",
        last_health_check=NOW,
        health_status=health_status,
        last_sync_at=last_sync_at or NOW - timedelta(hours=1),
        sync_error=sync_error,
    )


def _backfill(status: str = "completed") -> dict[str, Any]:
    return {
        "status": status,
        "requested_start": "2025-05-01",
        "requested_end": "2026-05-24",
        "records_discovered": 100,
        "records_imported": 100 if status == "completed" else 60,
        "records_skipped": 0,
        "records_failed": 0 if status == "completed" else 5,
        "last_run_at": "2026-05-24T10:00:00+00:00",
        "blocking_reason": "Vendor export failed" if status in {"failed", "blocked"} else None,
    }


def _contract_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "idempotency_supported": True,
        "retry_budget": {
            "max_attempts": 3,
            "attempts_used": 0,
            "remaining_attempts": 3,
            "idempotency_supported": True,
            "idempotency_key": IDEMPOTENCY_KEY,
        },
    }
    payload.update(overrides)
    return payload


def _hubspot_mapping() -> dict[str, Any]:
    return {
        "lifecycle_stages": {
            "source_field": "lifecyclestage",
            "stage_map": {"mql": "marketingqualifiedlead", "sql": "salesqualifiedlead"},
            "updated_at": MAPPING_TS,
        },
        "opportunity_revenue": {
            "amount_field": "amount",
            "close_date_field": "closedate",
            "currency_field": "deal_currency_code",
            "updated_at": MAPPING_TS,
        },
        "account_domains": {"domain_field": "website", "updated_at": MAPPING_TS},
        "consent_unsubscribe": {
            "consent_field": "marketing_opt_in",
            "unsubscribe_field": "unsubscribed",
            "updated_at": MAPPING_TS,
        },
        "fiscal_calendar": {"fiscal_year_start_month": 4, "updated_at": MAPPING_TS},
        "currency": {"currency": "USD", "updated_at": MAPPING_TS},
        "timezone": {"timezone": "Asia/Kolkata", "updated_at": MAPPING_TS},
    }


def _campaign_mapping() -> dict[str, Any]:
    return {
        "campaign_ids": {"campaign_id_field": "campaign_id", "updated_at": MAPPING_TS},
        "utm_fields": {
            "source": "utm_source",
            "medium": "utm_medium",
            "campaign": "utm_campaign",
            "updated_at": MAPPING_TS,
        },
    }


def _mailchimp_mapping() -> dict[str, Any]:
    return {
        **_campaign_mapping(),
        "consent_unsubscribe": {
            "consent_field": "marketing_permission",
            "unsubscribe_field": "unsubscribe_status",
            "updated_at": MAPPING_TS,
        },
    }


def _base_configs(
    *,
    include_wordpress: bool = False,
    include_buffer: bool = False,
    include_brandwatch: bool = False,
) -> list[SimpleNamespace]:
    configs = [
        _connector_config(
            "hubspot",
            config={
                "owner": "revops@example.com",
                "account_id": "portal-123",
                "granted_scopes": [
                    "crm.objects.contacts.read",
                    "crm.objects.deals.read",
                    "automation",
                ],
                "data_coverage_status": "ready",
                "marketing_field_mapping": _hubspot_mapping(),
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract_payload(),
            },
        ),
        _connector_config(
            "google_ads",
            config={
                "owner": "growth@example.com",
                "customer_id": "123-456-7890",
                "granted_scopes": ["https://www.googleapis.com/auth/adwords"],
                "marketing_field_mapping": _campaign_mapping(),
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract_payload(),
            },
        ),
        _connector_config(
            "ga4",
            config={
                "owner": "analytics@example.com",
                "property_id": "properties/123",
                "granted_scopes": ["https://www.googleapis.com/auth/analytics.readonly"],
                "marketing_field_mapping": _campaign_mapping(),
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract_payload(),
            },
        ),
        _connector_config(
            "mailchimp",
            config={
                "owner": "email@example.com",
                "audience_id": "aud-1",
                "marketing_field_mapping": _mailchimp_mapping(),
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract_payload(),
            },
        ),
    ]
    if include_wordpress:
        configs.append(
            _connector_config(
                "wordpress",
                config={
                    "owner": "web@example.com",
                    "site_url": "https://www.agenticorg.ai",
                    "marketing_field_mapping": _campaign_mapping(),
                    "marketing_backfill": _backfill(),
                    "marketing_connector_contract": _contract_payload(),
                },
            )
        )
    if include_buffer:
        configs.append(
            _connector_config(
                "buffer",
                config={
                    "owner": "social@example.com",
                    "granted_scopes": ["profile:read", "updates:create"],
                    "marketing_field_mapping": _campaign_mapping(),
                    "marketing_backfill": _backfill(),
                    "marketing_connector_contract": _contract_payload(),
                },
            )
        )
    if include_brandwatch:
        configs.append(
            _connector_config(
                "brandwatch",
                config={
                    "owner": "brand@example.com",
                    "project_id": "brand-project-123",
                    "granted_scopes": ["read_mentions", "read_queries"],
                    "marketing_field_mapping": {
                        "timezone": {"timezone": "Asia/Kolkata", "updated_at": MAPPING_TS},
                    },
                    "marketing_backfill": _backfill(),
                    "marketing_connector_contract": _contract_payload(),
                },
            )
        )
    return configs


def _workflow_config(
    *,
    mode: str = "shadow",
    promoted: bool = False,
    approval_owner: str | None = "cmo@example.com",
    policy_owner: str | None = "legal@example.com",
) -> dict[str, Any]:
    return {
        "mode": mode,
        "promoted": promoted,
        "approval_owner": approval_owner,
        "policy_owner": policy_owner,
        "shadow_quality": {
            "status": "passed",
            "sample_count": 5,
            "success_rate": 0.95,
            "last_run_at": "2026-05-24T09:00:00+00:00",
        },
    }


def _with_workflow(
    configs: list[SimpleNamespace],
    workflow_key: str,
    payload: dict[str, Any],
) -> list[SimpleNamespace]:
    cloned = deepcopy(configs)
    workflows = cloned[0].config.setdefault("marketing_workflows", {})
    workflows[workflow_key] = payload
    return cloned


def _source_facts(**overrides: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "last_updated_at": FRESH_TS,
        "total_marketing_spend": 1_000.0,
        "ad_spend": 1_000.0,
        "campaign_spend_by_channel": {"google": 600.0, "linkedin": 250.0, "meta": 150.0},
        "new_customers": 10,
        "lifecycle_stage_counts": {
            "visitor": 1_000,
            "lead": 250,
            "mql": 100,
            "sql": 40,
            "opportunity": 20,
            "customer": 10,
        },
        "ad_platform_conversions": 100,
        "crm_campaign_attributed_mqls": 100,
        "ga4_conversion_events": 250,
        "crm_leads_created": 250,
        "attributed_revenue": 5_000.0,
        "opportunities": [{"amount": 2_500.0}, {"amount": 1_500.0}],
        "customer_ltv": 2_000.0,
        "completed_experiments": 6,
        "period_days": 30,
        "content_sessions": 1_000,
        "content_conversions": 50,
        "wordpress_content_sessions": 1_000,
        "ga4_content_sessions": 1_000,
        "emails_sent": 2_000,
        "emails_opened": 900,
        "emails_clicked": 300,
        "emails_unsubscribed": 20,
        "crm_email_sends": 2_000,
        "crm_email_clicks": 300,
        "crm_unsubscribed_contacts": 20,
        "brand_positive_mentions": 70,
        "brand_negative_mentions": 10,
        "brand_neutral_mentions": 20,
        "intent_ready_accounts": 25,
        "target_accounts": 100,
        "abm_target_account_domains": ["alpha.example", "beta.example"],
        "crm_account_domains": ["alpha.example", "beta.example"],
        "intent_account_domains": ["alpha.example", "beta.example"],
        "source_currencies": {"ads": "USD", "crm": "USD", "finance": "USD"},
        "source_timezones": {
            "ads": "Asia/Kolkata",
            "crm": "Asia/Kolkata",
            "analytics": "Asia/Kolkata",
            "email": "Asia/Kolkata",
        },
        "source_refs": [{"connector_key": "hubspot", "object": "deals"}],
        "audit_refs": ["audit-kpi-source"],
    }
    facts.update(overrides)
    return facts


def _approval(**overrides: Any) -> dict[str, Any]:
    approval = {
        "approval_id": "apr-e2e-1",
        "workflow_id": "campaign_launch",
        "workflow_run_id": "run-e2e-1",
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
        "preview_payload": {"campaign_name": "Pipeline Sprint", "channels": ["google_ads"]},
        "before_after_diff": {
            "before": "Campaign is draft only",
            "after": "Campaign launches to selected audience",
        },
        "budget_impact": {"amount": 1200, "currency": "USD", "period": "daily"},
        "audience_impact": {"estimated_recipients": 25000, "segments": ["enterprise_search"]},
        "brand_legal_risk_flags": ["budget_change"],
        "source_refs": [{"type": "campaign_brief", "id": "brief-1"}],
        "connector_key": "google_ads",
        "agent_rationale": "Shadow recommendations exceeded launch threshold.",
        "audit_refs": ["audit-approval-context"],
        "rollback_stop_plan": {"summary": "Pause campaign and restore previous budget cap."},
        "external_write_required": True,
        "customer_facing": True,
        "workflow_mode": "active",
    }
    approval.update(overrides)
    return approval


def _operating_projection(
    configs: list[SimpleNamespace],
    *,
    source_data: dict[str, Any] | None = None,
    source_context: dict[str, Any] | None = None,
    approval_records: list[dict[str, Any]] | None = None,
    external_write_results: list[dict[str, Any]] | None = None,
    report_types: tuple[str, ...] | None = None,
    production_tenant: bool = False,
) -> dict[str, Any]:
    source_context = source_context or {"demo": False, "source": "computed"}
    approval_records = approval_records or []
    setup = build_marketing_connector_setup(configs, now=NOW)
    contracts = build_marketing_connector_contracts(setup, configs, now=NOW)
    readiness = build_marketing_data_readiness(
        setup,
        configs,
        connector_contracts=contracts,
        now=NOW,
    )
    activation = build_cmo_workflow_activation(setup, readiness, configs, now=NOW)
    policy_projection = build_marketing_policy_projection(configs)
    escalation_projection = build_marketing_escalation_projection(configs)
    audit_projection = build_marketing_decision_audit_projection(configs)
    unified = build_unified_cmo_kpi_projection(
        connector_setup=setup,
        data_readiness=readiness,
        connector_contracts=contracts,
        connector_configs=configs,
        source_data=source_data or _source_facts(),
        now=NOW,
    )
    report_quality = build_cmo_report_quality_projection(
        kpi_results=unified["unified_cmo_kpi_results"],
        reconciliation_checks=unified["cmo_kpi_reconciliation_checks"],
        connector_setup=setup,
        data_readiness=readiness,
        connector_contracts=contracts,
        workflow_activation=activation,
        policy_projection=policy_projection,
        escalation_projection=escalation_projection,
        decision_audit_projection=audit_projection,
        source_context=source_context,
        report_types=report_types,
        production_tenant=production_tenant,
        now=NOW,
    )
    timeout_risk = build_approval_timeout_risk(approval_records, now=NOW)
    approval_review = build_cmo_approval_review_projection(
        approval_records,
        connector_contracts=contracts,
        policy_projection=policy_projection,
        escalation_projection=escalation_projection,
        decision_audit_projection=audit_projection,
        approval_timeout_risk=timeout_risk,
        now=NOW,
    )
    work_queue = build_cmo_work_queue_projection(
        approval_timeout_risk=timeout_risk,
        escalation_projection=escalation_projection,
        connector_setup=setup,
        connector_contracts=contracts,
        data_readiness=readiness,
        workflow_activation=activation,
        policy_projection=policy_projection,
        decision_audit_projection=audit_projection,
        kpi_results=unified["unified_cmo_kpi_results"],
        reconciliation_checks=unified["cmo_kpi_reconciliation_checks"],
        report_quality_gates=report_quality["report_quality_gates"],
        external_write_results=external_write_results or [],
        source_context=source_context,
        now=NOW,
    )
    drilldown = build_cmo_kpi_drilldown_projection(
        kpi_schema=unified["unified_cmo_kpi_schema"],
        kpi_results=unified["unified_cmo_kpi_results"],
        reconciliation_checks=unified["cmo_kpi_reconciliation_checks"],
        connector_setup=setup,
        data_readiness=readiness,
        connector_contracts=contracts,
        work_queue=work_queue["cmo_work_queue"],
        report_quality_gates=report_quality["report_quality_gates"],
        source_data=source_data or _source_facts(),
        source_context=source_context,
        now=NOW,
    )
    return {
        "connector_setup": setup,
        "connector_contracts": contracts,
        "data_readiness": readiness,
        "workflow_activation": activation,
        "policy_projection": policy_projection,
        "escalation_projection": escalation_projection,
        "audit_projection": audit_projection,
        "approval_timeout_risk": timeout_risk,
        "approval_review": approval_review,
        **unified,
        **report_quality,
        **work_queue,
        **drilldown,
    }


def _report_gate(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in payload["report_quality_gates"] if item["report_key"] == key)


def _workflow_row(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(
        item
        for item in payload["workflow_activation"]["workflow_activation_status"]
        if item["workflow_key"] == key
    )


def _kpi(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in payload["unified_cmo_kpi_results"] if item["kpi_key"] == key)


def _drilldown(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in payload["cmo_kpi_drilldowns"] if item["kpi_key"] == key)


def _contract(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in payload["connector_contracts"] if item["connector_key"] == key)


def _write_step(
    *,
    agent_type: str = "campaign_pilot",
    action: str = "launch_campaign",
    connector_key: str = "google_ads",
) -> dict[str, Any]:
    return {
        "id": f"{action}_step",
        "type": "agent",
        "agent_type": agent_type,
        "action": action,
        "connector_key": connector_key,
        "external_write_required": True,
        "external_write_confirmation_required": True,
        "expected_confirmation_fields": ["external_object_id", "confirmed_at"],
        "idempotency_key": IDEMPOTENCY_KEY,
        "decision_audit_required": True,
    }


def _confirmed_write_output(**overrides: Any) -> dict[str, Any]:
    output = {
        "external_write_state": "accepted",
        "external_object_id": "customers/123/campaigns/456",
        "source_url": "https://ads.google.com/campaigns/456",
        "idempotency_key": IDEMPOTENCY_KEY,
        "request_fingerprint": "fp-e2e-confirmed",
    }
    output.update(overrides)
    return output


def test_weekly_marketing_review_blocks_delivery_and_surfaces_operator_work() -> None:
    configs = _with_workflow(
        _base_configs(),
        "weekly_marketing_report",
        _workflow_config(mode="active", promoted=True),
    )
    payload = _operating_projection(
        configs,
        source_data=_source_facts(
            new_customers=None,
            opportunities=[],
            campaign_spend_by_channel={"google": 450.0, "linkedin": 125.0},
        ),
        report_types=("weekly_marketing_report",),
        production_tenant=True,
    )

    gate = _report_gate(payload, "weekly_marketing_report")
    queue_categories = {item["category"] for item in payload["cmo_work_queue"]}

    assert payload["connector_setup"]
    assert payload["connector_contracts"]
    assert payload["data_readiness"]["field_mapping_status"]
    assert payload["workflow_activation"]["workflow_activation_status"]
    assert payload["policy_projection"]["marketing_policy_summary"]["status"] == "ready"
    assert payload["escalation_projection"]["marketing_escalation_summary"]["status"] == "ready"
    assert payload["audit_projection"]["marketing_decision_audit_summary"]["status"] == "ready"
    assert gate["status"] == "blocked"
    assert gate["safe_report_mode"] == "draft_only"
    assert gate["trusted_delivery_allowed"] is False
    assert "paid_spend_totals_by_channel" in gate["failed_reconciliation_keys"]
    assert _kpi(payload, "cac")["status"] == "blocked"
    assert {"report", "kpi", "reconciliation"} <= queue_categories
    assert payload["cmo_work_queue_summary"]["readiness"] in {"blocked", "needs_action"}
    cac_lineage = _drilldown(payload, "cac")
    assert cac_lineage["status"] == "blocked"
    assert cac_lineage["related_work_queue_item_ids"]
    assert cac_lineage["related_report_gate_ids"]


def test_campaign_launch_requires_readiness_approval_audit_and_confirmed_write() -> None:
    shadow_payload = _operating_projection(
        _with_workflow(_base_configs(), "campaign_launch", _workflow_config()),
        source_data=_source_facts(),
    )
    active_payload = _operating_projection(
        _with_workflow(
            _base_configs(),
            "campaign_launch",
            _workflow_config(mode="active", promoted=True),
        ),
        source_data=_source_facts(),
        approval_records=[_approval()],
        report_types=("campaign_performance_ad_hoc",),
    )
    google_contract = _contract(active_payload, "google_ads")
    workflow = {
        "id": "wf_campaign_launch_e2e",
        "domain": "marketing",
        "mode": "production",
        "steps": [_write_step()],
    }
    lint = lint_marketing_workflow(workflow, connector_contracts=[google_contract])
    policy_decision = evaluate_marketing_policy(
        {
            "workflow_id": "campaign_launch",
            "workflow_mode": "active",
            "action": "launch_campaign",
            "external_write_required": True,
        }
    )
    audit_package = build_cmo_decision_audit_package(
        {
            "tenant_id": "tenant-1",
            "workflow_id": "campaign_launch",
            "workflow_run_id": "run-e2e-1",
            "step_id": "launch",
            "agent_type": "campaign_pilot",
            "action": "launch_campaign",
            "policy_result": policy_decision,
            "source_refs": [{"type": "campaign_brief", "id": "brief-1"}],
            "rationale": "Launch after successful shadow run.",
        },
        now=NOW,
    )
    unconfirmed = evaluate_marketing_external_write_result(
        [google_contract],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        step=_write_step(),
        state={
            "id": "run-e2e-1",
            "workflow_id": "campaign_launch",
            "workflow_mode": "active",
            "marketing_policy_approval_satisfied": True,
        },
        output={"external_write_state": "write_unconfirmed", "idempotency_key": IDEMPOTENCY_KEY},
        now=NOW,
    )
    confirmed = evaluate_marketing_external_write_result(
        [google_contract],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        step=_write_step(),
        state={
            "id": "run-e2e-1",
            "workflow_id": "campaign_launch",
            "workflow_mode": "active",
            "marketing_policy_approval_satisfied": True,
        },
        output=_confirmed_write_output(),
        now=NOW,
    )
    contract = build_marketing_agent_contract_output(
        "campaign_pilot",
        "launch_campaign",
        result={
            "status": "completed",
            "confidence": 0.93,
            "rationale": "Campaign launch is ready after policy approval.",
            "recommended_actions": ["Launch campaign after approval."],
            "audit_ref": audit_package["audit_reference"],
            "source_refs": [{"connector_key": "google_ads", "object": "campaign"}],
            "external_write_confirmation_status": "write_confirmed",
        },
        policy_result=policy_decision,
        audit_ref=audit_package["audit_reference"],
        workflow_mode="active",
    )

    assert _workflow_row(shadow_payload, "campaign_launch")["state"] == "promotion_ready"
    assert _workflow_row(active_payload, "campaign_launch")["state"] == "active"
    assert google_contract["write_ready"] is True
    assert google_contract["write_safe"] is True
    assert active_payload["data_readiness"]["kpi_readiness"]["status"] in {"ready", "degraded"}
    assert lint.has_errors is False
    assert policy_decision["decision"] == "requires_approval"
    assert active_payload["approval_review"]["cmo_approval_review_summary"]["approval_ready"] >= 1
    assert audit_package["audit_reference"].startswith("cmo_decision_audit:")
    assert unconfirmed["step_status"] == "failed"
    assert unconfirmed["final_state"] == "write_unconfirmed"
    assert confirmed["step_status"] == "completed"
    assert confirmed["final_state"] == "write_confirmed"
    assert contract_has_required_shape(contract)
    assert contract["external_writes_completed"] is True


def test_crisis_response_requires_escalation_timeout_review_and_audit_before_public_write() -> None:
    approval = _approval(
        approval_id="apr-crisis-1",
        workflow_id="brand_crisis_response",
        workflow_run_id="run-crisis-1",
        step_id="public_response",
        action="public_response",
        approval_type="crisis_public_response",
        agent_type="brand_monitor",
        connector_key="buffer",
        crisis_response=True,
        public_response=True,
        brand_legal_risk_flags=["crisis", "legal_claim"],
        created_at=(NOW - timedelta(hours=4)).isoformat(),
        due_at=(NOW - timedelta(hours=1)).isoformat(),
    )
    payload = _operating_projection(
        _with_workflow(
            _base_configs(include_buffer=True, include_brandwatch=True),
            "brand_crisis_response",
            _workflow_config(mode="active", promoted=True),
        ),
        approval_records=[approval],
        source_context={
            "demo": False,
            "source": "computed",
            "crisis_risk": {
                "risk_id": "brand-crisis-1",
                "title": "Potential public brand crisis",
                "public_response_required": True,
                "severity": "critical",
                "audit_refs": ["audit-crisis-risk"],
            },
        },
    )
    policy_decision = evaluate_marketing_policy(
        {
            "workflow_id": "brand_crisis_response",
            "workflow_mode": "active",
            "action": "public_response",
            "public_response": True,
            "external_write_required": True,
        }
    )
    escalation = evaluate_marketing_escalation(
        {
            "trigger_type": "crisis_public_response",
            "workflow_id": "brand_crisis_response",
            "workflow_run_id": "run-crisis-1",
            "step_id": "public_response",
            "severity": "critical",
        },
        now=NOW,
    )
    audit_package = build_cmo_decision_audit_package(
        {
            "workflow_id": "brand_crisis_response",
            "workflow_run_id": "run-crisis-1",
            "step_id": "public_response",
            "agent_type": "brand_monitor",
            "action": "public_response",
            "policy_result": policy_decision,
            "escalation_result": escalation,
            "approval_result": payload["approval_review"]["cmo_approval_reviews"][0],
            "risk_flags": ["crisis", "legal_claim"],
            "rationale": "Public response requires CMO, Legal, and CEO path.",
        },
        now=NOW,
    )
    unsafe_public_write = evaluate_marketing_external_write_result(
        [_contract(payload, "buffer")],
        connector_key="buffer",
        action="public_response",
        workflow_mode="active",
        step=_write_step(agent_type="brand_monitor", action="public_response", connector_key="buffer"),
        state={"id": "run-crisis-1", "workflow_id": "brand_crisis_response", "workflow_mode": "active"},
        output=_confirmed_write_output(
            external_object_id="updates/urgent-public-response",
            source_url="https://publish.buffer.com/updates/urgent-public-response",
        ),
        now=NOW,
    )

    assert _workflow_row(payload, "brand_crisis_response")["state"] == "active"
    assert policy_decision["decision"] == "requires_escalation"
    assert policy_decision["required_escalation_role"] == "ceo"
    assert escalation["decision"] in {"escalate", "escalate_to_legal"}
    assert escalation["audit_reference"].startswith("mkt_escalation_")
    assert payload["approval_timeout_risk"]["status"] == "blocked"
    review = payload["approval_review"]["cmo_approval_reviews"][0]
    assert review["action_type"] == "crisis_public_response"
    assert review["status"] in {"blocked", "timed_out", "escalated"}
    assert review["policy_result_ref"]
    assert review["escalation_result_ref"]
    assert audit_package["audit_reference"].startswith("cmo_decision_audit:")
    assert unsafe_public_write["step_status"] == "failed"
    assert unsafe_public_write["error_code"] in {
        "external_write_marketing_policy_approval_required",
        "external_write_approval_timeout_blocked",
    }
    assert "crisis_risk" in {item["category"] for item in payload["cmo_work_queue"]}


def test_abm_sprint_beta_agent_still_blocks_production_without_required_gates() -> None:
    payload = _operating_projection(
        _with_workflow(
            _base_configs(),
            "abm_sprint",
            _workflow_config(mode="active", promoted=True),
        ),
        source_data=_source_facts(),
    )
    production_lint = lint_marketing_workflow(
        {
            "id": "wf_abm_production",
            "domain": "marketing",
            "mode": "production",
            "steps": [
                {
                    "id": "aggregate_intent",
                    "type": "agent",
                    "agent_type": "abm_agent",
                    "action": "aggregate_intent",
                }
            ],
        }
    )
    target_lint = lint_marketing_workflow(
        {
            "id": "wf_abm_target",
            "domain": "marketing",
            "mode": "target",
            "steps": [
                {
                    "id": "aggregate_intent",
                    "type": "agent",
                    "agent_type": "abm_agent",
                    "action": "aggregate_intent",
                }
            ],
        }
    )
    contract = build_marketing_agent_contract_output(
        "abm_agent",
        "aggregate_intent",
        result={"status": "degraded", "rationale": "ABM core agent is beta without production proof."},
        workflow_mode="target",
        audit_ref="audit-abm-target-contract",
    )

    assert _workflow_row(payload, "abm_sprint")["state"] == "promotion_blocked"
    assert "Required ABM connector" in " ".join(_workflow_row(payload, "abm_sprint")["blocked_reasons"])
    assert production_lint.has_errors is True
    assert "marketing_agent_unavailable_for_production" not in {finding.code for finding in production_lint.errors}
    assert "marketing_agent_beta_in_production" in {finding.code for finding in production_lint.warnings}
    assert target_lint.has_errors is False
    assert "marketing_agent_unavailable_target_only" not in {finding.code for finding in target_lint.warnings}
    assert contract_has_required_shape(contract)
    assert contract["maturity"] == "beta"
    assert contract["production_ready"] is False
    assert contract["external_write_confirmation_status"] == "not_required"


def test_content_production_to_approval_to_publish_fails_closed_until_write_confirmed() -> None:
    approval = _approval(
        approval_id="apr-content-1",
        workflow_id="content_pipeline",
        workflow_run_id="run-content-1",
        step_id="publish",
        action="publish_to_wordpress",
        approval_type="content_publish",
        agent_type="content_factory",
        connector_key="wordpress",
        preview_payload={"title": "How AI agents improve marketing operations"},
        before_after_diff={"before": "Draft only", "after": "Published WordPress post"},
        brand_legal_risk_flags=["brand_claim"],
        source_refs=[{"type": "content_brief", "id": "brief-content-1"}],
        audit_refs=["audit-content-approval"],
    )
    payload = _operating_projection(
        _with_workflow(
            _base_configs(include_wordpress=True),
            "content_pipeline",
            _workflow_config(mode="active", promoted=True),
        ),
        source_data=_source_facts(),
        approval_records=[approval],
        report_types=("campaign_performance_ad_hoc",),
    )
    wordpress_contract = _contract(payload, "wordpress")
    draft_contract = build_marketing_agent_contract_output(
        "content_factory",
        "create_draft",
        result={
            "status": "completed",
            "confidence": 0.88,
            "rationale": "Draft matches the campaign brief.",
            "recommended_actions": ["Send draft to CMO approval."],
            "audit_ref": "mkt_audit_content_draft",
            "source_refs": [{"type": "content_brief", "id": "brief-content-1"}],
            "external_write_confirmation_status": "not_required",
        },
        audit_ref="mkt_audit_content_draft",
        workflow_mode="shadow",
    )
    publish_policy = evaluate_marketing_policy(
        {
            "workflow_id": "content_pipeline",
            "workflow_mode": "active",
            "action": "publish_to_wordpress",
            "external_write_required": True,
        }
    )
    unconfirmed_publish = evaluate_marketing_external_write_result(
        [wordpress_contract],
        connector_key="wordpress",
        action="publish_to_wordpress",
        workflow_mode="active",
        step=_write_step(
            agent_type="content_factory",
            action="publish_to_wordpress",
            connector_key="wordpress",
        ),
        state={
            "id": "run-content-1",
            "workflow_id": "content_pipeline",
            "workflow_mode": "active",
            "marketing_policy_approval_satisfied": True,
        },
        output={"external_write_state": "write_unconfirmed", "idempotency_key": IDEMPOTENCY_KEY},
        now=NOW,
    )
    confirmed_publish = evaluate_marketing_external_write_result(
        [wordpress_contract],
        connector_key="wordpress",
        action="publish_to_wordpress",
        workflow_mode="active",
        step=_write_step(
            agent_type="content_factory",
            action="publish_to_wordpress",
            connector_key="wordpress",
        ),
        state={
            "id": "run-content-1",
            "workflow_id": "content_pipeline",
            "workflow_mode": "active",
            "marketing_policy_approval_satisfied": True,
        },
        output=_confirmed_write_output(
            external_object_id="wp-post-4242",
            source_url="https://www.agenticorg.ai/blog/ai-agents-marketing",
        ),
        now=NOW,
    )
    publish_contract = build_marketing_agent_contract_output(
        "content_factory",
        "publish_to_wordpress",
        result={
            "status": "completed",
            "confidence": 0.86,
            "rationale": "Publishing can complete only after confirmation.",
            "recommended_actions": ["Publish after approval."],
            "audit_ref": "mkt_audit_content_publish",
            "source_refs": [{"connector_key": "wordpress", "object": "post"}],
            "external_write_confirmation_status": "write_confirmed",
        },
        policy_result=publish_policy,
        audit_ref="mkt_audit_content_publish",
        workflow_mode="active",
    )
    lint = lint_marketing_workflow(
        {
            "id": "wf_content_publish_e2e",
            "domain": "marketing",
            "mode": "production",
            "steps": [
                _write_step(
                    agent_type="content_factory",
                    action="publish_to_wordpress",
                    connector_key="wordpress",
                )
            ],
        },
        connector_contracts=[wordpress_contract],
    )

    assert _workflow_row(payload, "content_pipeline")["state"] in {"active", "promotion_ready"}
    assert contract_has_required_shape(draft_contract)
    assert draft_contract["maturity"] == "beta"
    assert draft_contract["shadow_read_only"] is True
    assert draft_contract["external_writes_completed"] is False
    assert publish_policy["decision"] == "requires_approval"
    assert payload["approval_review"]["cmo_approval_reviews"][0]["action_type"] == "content_publish"
    assert "approve" in payload["approval_review"]["cmo_approval_reviews"][0]["allowed_reviewer_actions"]
    assert wordpress_contract["write_safe"] is True
    assert unconfirmed_publish["step_status"] == "failed"
    assert unconfirmed_publish["final_state"] == "write_unconfirmed"
    assert confirmed_publish["step_status"] == "completed"
    assert confirmed_publish["final_state"] == "write_confirmed"
    assert contract_has_required_shape(publish_contract)
    assert publish_contract["external_writes_completed"] is True
    assert lint.has_errors is False
