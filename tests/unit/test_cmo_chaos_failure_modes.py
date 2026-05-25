from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

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
STALE_TS = (NOW - timedelta(days=3)).isoformat()
MAPPING_TS = "2026-05-01T00:00:00+00:00"
IDEMPOTENCY_KEY = "mkt-chaos-idempotency-001"


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
        "records_imported": 100 if status == "completed" else 55,
        "records_skipped": 0,
        "records_failed": 0 if status == "completed" else 6,
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
            "next_retry_at": "2026-05-24T12:05:00+00:00",
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


def _base_configs() -> list[SimpleNamespace]:
    return [
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
                "marketing_connector_contract": _contract_payload(write_capabilities=[], required_write_scopes=[]),
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
        _connector_config(
            "wordpress",
            config={
                "owner": "web@example.com",
                "site_url": "https://www.agenticorg.ai",
                "marketing_field_mapping": _campaign_mapping(),
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract_payload(),
            },
        ),
    ]


def _workflow_config(**overrides: Any) -> dict[str, Any]:
    payload = {
        "mode": "active",
        "promoted": True,
        "approval_owner": "cmo@example.com",
        "policy_owner": "legal@example.com",
        "shadow_quality": {
            "status": "passed",
            "sample_count": 6,
            "success_rate": 0.95,
            "last_run_at": "2026-05-24T09:00:00+00:00",
        },
    }
    payload.update(overrides)
    return payload


def _with_workflow(
    configs: list[SimpleNamespace],
    workflow_key: str,
    payload: dict[str, Any] | None = None,
) -> list[SimpleNamespace]:
    cloned = deepcopy(configs)
    cloned[0].config.setdefault("marketing_workflows", {})[workflow_key] = payload or _workflow_config()
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
        "approval_id": "apr-chaos-1",
        "workflow_id": "campaign_launch",
        "workflow_run_id": "run-chaos-1",
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
        "preview_payload": {"campaign_name": "Chaos-safe launch", "channels": ["google_ads"]},
        "before_after_diff": {
            "before": "Campaign remains draft only",
            "after": "Campaign launches to selected audience",
        },
        "budget_impact": {"amount": 1200, "currency": "USD", "period": "daily"},
        "audience_impact": {"estimated_recipients": 25000, "segments": ["enterprise_search"]},
        "brand_legal_risk_flags": ["budget_change"],
        "source_refs": [{"type": "campaign_brief", "id": "brief-chaos"}],
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
    activation = build_cmo_workflow_activation(
        setup,
        readiness,
        configs,
        connector_contracts=contracts,
        now=NOW,
    )
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
    }


def _contract_row(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(row for row in payload["connector_contracts"] if row["connector_key"] == key)


def _workflow_row(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(
        row
        for row in payload["workflow_activation"]["workflow_activation_status"]
        if row["workflow_key"] == key
    )


def _kpi(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(row for row in payload["unified_cmo_kpi_results"] if row["kpi_key"] == key)


def _reconciliation(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(
        row
        for row in payload["cmo_kpi_reconciliation_checks"]
        if row["reconciliation_key"] == key
    )


def _report_gate(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(row for row in payload["report_quality_gates"] if row["report_key"] == key)


def _work_item_categories(payload: dict[str, Any]) -> set[str]:
    return {item["category"] for item in payload["cmo_work_queue"]}


def _external_write_step(
    *,
    action: str = "launch_campaign",
    connector_key: str = "google_ads",
    agent_type: str = "campaign_pilot",
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
        "inputs": {"campaign_name": "Chaos launch", "budget": 1200},
    }


def _direct_write_contract(
    *,
    idempotency_key_supported: bool = True,
    remaining_attempts: int = 2,
    confirmations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "connector_key": "google_ads",
        "category": "Ads",
        "read_ready": True,
        "write_ready": True,
        "write_safe": True,
        "idempotency_key_supported": idempotency_key_supported,
        "blocks_external_writes": False,
        "retry_budget": {
            "max_attempts": 3,
            "attempts_used": 1,
            "remaining_attempts": remaining_attempts,
            "next_retry_at": "2026-05-24T12:05:00+00:00",
            "idempotency_supported": idempotency_key_supported,
            "idempotency_key": IDEMPOTENCY_KEY if idempotency_key_supported else None,
        },
        "external_write_confirmations": confirmations or [],
    }


@pytest.mark.parametrize(
    ("failure_state", "expected_status", "expected_cta"),
    [
        ("timeout", "degraded", "review_retry_budget"),
        ("vendor_5xx", "degraded", "monitor_vendor_recovery"),
        ("rate_limited", "degraded", "review_retry_budget"),
        ("quota_exhausted", "degraded", "wait_for_quota_reset"),
        ("malformed_payload", "blocked", "fix_connector_payload"),
    ],
)
def test_connector_outage_rate_limit_quota_and_malformed_payload_fail_safely(
    failure_state: str,
    expected_status: str,
    expected_cta: str,
) -> None:
    configs = _with_workflow(_base_configs(), "campaign_launch")
    configs[1].config["marketing_connector_contract"] = _contract_payload(
        state=failure_state,
        retry_budget={
            "attempts_used": 1,
            "idempotency_supported": True,
            "idempotency_key": IDEMPOTENCY_KEY,
        },
    )
    payload = _operating_projection(configs, report_types=("campaign_performance_ad_hoc",))
    google_ads = _contract_row(payload, "google_ads")
    campaign = _workflow_row(payload, "campaign_launch")
    lint = lint_marketing_workflow(
        {
            "id": "wf_connector_chaos",
            "domain": "marketing",
            "mode": "production",
            "steps": [_external_write_step()],
        },
        connector_contracts=[google_ads],
    )
    write_result = evaluate_marketing_external_write_result(
        [google_ads],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        step=_external_write_step(),
        state={
            "workflow_id": "campaign_launch",
            "workflow_run_id": "run-connector-chaos",
            "marketing_policy_approval_satisfied": True,
        },
        output={
            "external_write_state": "accepted",
            "external_object_id": "customers/123/campaigns/456",
            "idempotency_key": IDEMPOTENCY_KEY,
        },
        now=NOW,
    )
    contract_output = build_marketing_agent_contract_output(
        "campaign_pilot",
        "launch_campaign",
        result={
            "status": "blocked",
            "confidence": 0.0,
            "rationale": "Connector failure chaos run must not complete launch.",
            "recommended_actions": [expected_cta],
            "audit_ref": "audit-connector-chaos",
            "source_refs": [{"connector_key": "google_ads", "failure_state": failure_state}],
            "external_write_confirmation_status": "write_unconfirmed",
        },
        audit_ref="audit-connector-chaos",
        workflow_mode="active",
        connector_ready=google_ads["read_ready"],
    )

    assert google_ads["failure_class"] == failure_state
    assert google_ads["read_status"] == expected_status
    assert google_ads["write_safe"] is False
    assert google_ads["retry_policy"]["failure_class"] == failure_state
    assert google_ads["next_action_cta"] == expected_cta
    assert campaign["state"] in {"promotion_blocked", "degraded"}
    assert lint.has_errors is True
    assert write_result["step_status"] == "failed"
    assert write_result["error_code"] == "external_write_connector_not_write_safe"
    assert "connector" in _work_item_categories(payload)
    assert contract_has_required_shape(contract_output)
    assert contract_output["external_writes_completed"] is False


def test_auth_expired_and_insufficient_scope_create_actionable_setup_states() -> None:
    auth_configs = _with_workflow(_base_configs(), "campaign_launch")
    auth_configs[1].config["marketing_connector_contract"] = _contract_payload(auth_status="expired")
    auth_payload = _operating_projection(auth_configs)
    expired = _contract_row(auth_payload, "google_ads")

    scope_configs = _with_workflow(_base_configs(), "campaign_launch")
    scope_configs[1].config["marketing_connector_contract"] = _contract_payload(
        required_write_scopes=["ads.write"],
        write_capabilities=["campaigns.mutate"],
    )
    scope_payload = _operating_projection(scope_configs)
    insufficient = _contract_row(scope_payload, "google_ads")
    scope_lint = lint_marketing_workflow(
        {
            "id": "wf_scope_chaos",
            "domain": "marketing",
            "mode": "production",
            "steps": [_external_write_step()],
        },
        connector_contracts=[insufficient],
    )

    assert expired["contract_state"] == "auth_expired"
    assert expired["read_ready"] is False
    assert expired["write_safe"] is False
    assert expired["next_action_cta"] == "reconnect"
    assert _workflow_row(auth_payload, "campaign_launch")["state"] == "promotion_blocked"
    assert "connector" in _work_item_categories(auth_payload)
    assert insufficient["failure_class"] == "insufficient_scope"
    assert insufficient["read_ready"] is True
    assert insufficient["write_safe"] is False
    assert insufficient["missing_write_scopes"] == ["ads.write"]
    assert insufficient["next_action_cta"] == "add_scope"
    assert scope_lint.has_errors is True
    assert "marketing_external_write_connector_not_ready" in {
        finding.code for finding in scope_lint.errors
    }


def test_stale_and_partial_data_windows_are_explicit_degraded_not_hidden_success() -> None:
    stale_configs = _with_workflow(_base_configs(), "weekly_marketing_report")
    stale_configs[1].config["marketing_connector_contract"] = _contract_payload(
        ttl_seconds=60,
        last_sync_at=(NOW - timedelta(hours=2)).isoformat(),
    )
    stale_payload = _operating_projection(
        stale_configs,
        source_data=_source_facts(last_updated_at=STALE_TS),
        report_types=("weekly_marketing_report",),
        production_tenant=True,
    )
    stale_google = _contract_row(stale_payload, "google_ads")
    stale_gate = _report_gate(stale_payload, "weekly_marketing_report")

    partial_configs = _with_workflow(_base_configs(), "weekly_marketing_report")
    partial_configs[2].config["marketing_connector_contract"] = _contract_payload(state="partial_data")
    partial_payload = _operating_projection(partial_configs, report_types=("weekly_marketing_report",))
    partial_check = _reconciliation(partial_payload, "source_freshness_and_partial_data")

    assert stale_google["failure_class"] == "stale_data"
    assert stale_google["data_freshness"]["status"] == "stale"
    assert stale_payload["data_readiness"]["kpi_readiness"]["status"] == "degraded"
    assert stale_gate["status"] == "blocked"
    assert stale_gate["safe_report_mode"] == "draft_only"
    assert stale_gate["trusted_delivery_allowed"] is False
    assert "connector" in _work_item_categories(stale_payload)
    assert _contract_row(partial_payload, "ga4")["failure_class"] == "partial_data"
    assert partial_payload["data_readiness"]["kpi_readiness"]["status"] == "degraded"
    assert partial_check["status"] == "warning"
    assert partial_check["confidence_impact"] > 0
    assert _kpi(partial_payload, "content_performance")["status"] == "degraded"


def test_approval_timeout_blocks_review_external_write_and_queue() -> None:
    overdue_approval = _approval(
        approval_id="apr-timeout-chaos",
        created_at=(NOW - timedelta(hours=5)).isoformat(),
        due_at=(NOW - timedelta(hours=1)).isoformat(),
    )
    payload = _operating_projection(
        _with_workflow(_base_configs(), "campaign_launch"),
        approval_records=[overdue_approval],
    )
    timeout_decision = payload["approval_timeout_risk"]["approval_timeout_decisions"][0]
    review = payload["approval_review"]["cmo_approval_reviews"][0]
    write_result = evaluate_marketing_external_write_result(
        [_contract_row(payload, "google_ads")],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        step=_external_write_step(),
        state={"workflow_id": "campaign_launch", "workflow_run_id": "run-timeout-chaos"},
        output={
            "external_write_state": "write_confirmed",
            "external_object_id": "customers/123/campaigns/456",
            "idempotency_key": IDEMPOTENCY_KEY,
            "approval_timeout_decision": timeout_decision,
        },
        now=NOW,
    )

    assert timeout_decision["timed_out"] is True
    assert timeout_decision["external_writes_allowed"] is False
    assert timeout_decision["audit_evidence"]["audit_reference"].startswith("mkt_approval_timeout_")
    assert review["status"] == "timed_out"
    assert "approve" not in review["allowed_reviewer_actions"]
    assert write_result["step_status"] == "failed"
    assert write_result["error_code"] == "external_write_approval_timeout"
    assert {"approval", "escalation"} <= _work_item_categories(payload)


def test_budget_overspend_race_requires_policy_escalation_and_preserves_audit() -> None:
    policy = evaluate_marketing_policy(
        {
            "workflow_id": "daily_spend_optimization",
            "workflow_mode": "active",
            "action": "mutate_ad_budget",
            "channel": "google_ads",
            "budget_delta": 7_500,
            "external_write_required": True,
        }
    )
    escalation = evaluate_marketing_escalation(
        {
            "trigger_type": "budget_threshold_exceeded",
            "workflow_id": "daily_spend_optimization",
            "action": "mutate_ad_budget",
            "severity": "high",
        },
        now=NOW,
    )
    audit_package = build_cmo_decision_audit_package(
        {
            "workflow_id": "daily_spend_optimization",
            "workflow_run_id": "run-budget-chaos",
            "step_id": "budget_update",
            "agent_type": "campaign_pilot",
            "action": "mutate_ad_budget",
            "input_snapshot": {
                "previous_budget": 2_500,
                "requested_budget": 10_000,
                "budget_cap": 5_000,
            },
            "policy_result": policy,
            "escalation_result": escalation,
            "rationale": "Concurrent budget update exceeded the configured channel cap.",
            "risk_flags": ["budget_overspend_race"],
            "final_outcome": "rejected",
        },
        now=NOW,
    )
    rejected_write = evaluate_marketing_external_write_result(
        [_direct_write_contract()],
        connector_key="google_ads",
        action="mutate_ad_budget",
        workflow_mode="active",
        step=_external_write_step(action="mutate_ad_budget"),
        state={
            "workflow_id": "daily_spend_optimization",
            "workflow_run_id": "run-budget-chaos",
            "marketing_policy_approval_satisfied": True,
        },
        output={
            "external_write_state": "rejected",
            "rejection_reason": "Vendor rejected payload because budget cap was exceeded.",
            "next_action": "request_budget_approval",
            "idempotency_key": IDEMPOTENCY_KEY,
        },
        now=NOW,
    )
    queue = build_cmo_work_queue_projection(
        external_write_results=[rejected_write],
        source_context={"demo": False, "source": "computed"},
        now=NOW,
    )
    contract_output = build_marketing_agent_contract_output(
        "campaign_pilot",
        "mutate_ad_budget",
        result={
            "status": "blocked",
            "confidence": 0.0,
            "rationale": "Budget race detected; external update rejected.",
            "recommended_actions": ["request_budget_approval"],
            "audit_ref": audit_package["audit_reference"],
            "source_refs": [{"connector_key": "google_ads", "object": "budget"}],
            "external_write_confirmation_status": "write_unconfirmed",
        },
        policy_result=policy,
        audit_ref=audit_package["audit_reference"],
        workflow_mode="active",
    )

    assert policy["decision"] == "requires_approval"
    assert policy["next_action_cta"] == "request_budget_approval"
    assert escalation["decision"] == "escalate_to_finance"
    assert audit_package["input_snapshot_hash"]
    assert rejected_write["step_status"] == "failed"
    assert rejected_write["final_state"] == "rejected"
    assert rejected_write["escalation_decision"]["trigger_type"] == "external_write_rejected"
    assert queue["cmo_work_queue"][0]["category"] == "external_write"
    assert queue["cmo_work_queue"][0]["severity"] == "critical"
    assert contract_has_required_shape(contract_output)
    assert contract_output["approval_required"] is True
    assert contract_output["external_writes_completed"] is False


def test_duplicate_event_replay_schedules_retry_then_idempotently_recovers_without_duplicate() -> None:
    retry_scheduled = evaluate_marketing_external_write_result(
        [_direct_write_contract()],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        step=_external_write_step(),
        state={
            "workflow_id": "campaign_launch",
            "workflow_run_id": "run-replay-chaos",
            "marketing_policy_approval_satisfied": True,
        },
        output={
            "external_write_state": "timeout_unknown",
            "idempotency_key": IDEMPOTENCY_KEY,
            "request_fingerprint": "fp-replay-timeout",
        },
        now=NOW,
    )
    recovered = evaluate_marketing_external_write_result(
        [
            _direct_write_contract(
                confirmations=[
                    {
                        "action": "launch_campaign",
                        "status": "write_confirmed",
                        "idempotency_key": IDEMPOTENCY_KEY,
                        "external_object_id": "customers/123/campaigns/456",
                        "source_url": "https://ads.google.com/campaigns/456",
                        "confirmed_at": "2026-05-24T12:01:00+00:00",
                    }
                ]
            )
        ],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        step=_external_write_step(),
        state={
            "workflow_id": "campaign_launch",
            "workflow_run_id": "run-replay-chaos",
            "marketing_policy_approval_satisfied": True,
        },
        output={
            "external_write_state": "timeout_unknown",
            "idempotency_key": IDEMPOTENCY_KEY,
            "request_fingerprint": "fp-replay-duplicate",
        },
        now=NOW,
    )

    assert retry_scheduled["step_status"] == "waiting_delay"
    assert retry_scheduled["final_state"] == "retry_scheduled"
    assert retry_scheduled["retry_plan"]["duplicate_policy"] == "reuse_idempotency_key"
    assert retry_scheduled["resume_at"] == "2026-05-24T12:05:00+00:00"
    assert recovered["step_status"] == "completed"
    assert recovered["final_state"] == "idempotent_recovered"
    assert recovered["confirmation"]["external_object_id"] == "customers/123/campaigns/456"
    assert recovered["audit_events"][-1]["decision_audit"]["event_type"] == "external_write_idempotent_recovery"


def test_external_write_timeout_unknown_without_idempotency_and_rejection_fail_closed() -> None:
    timeout_unknown = evaluate_marketing_external_write_result(
        [_direct_write_contract(idempotency_key_supported=False)],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        step={**_external_write_step(), "idempotency_key": None},
        state={
            "workflow_id": "campaign_launch",
            "workflow_run_id": "run-timeout-write-chaos",
            "marketing_policy_approval_satisfied": True,
        },
        output={
            "external_write_state": "timeout_unknown",
            "request_fingerprint": "fp-timeout-no-idempotency",
        },
        now=NOW,
    )
    rejected = evaluate_marketing_external_write_result(
        [_direct_write_contract()],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        step=_external_write_step(),
        state={
            "workflow_id": "campaign_launch",
            "workflow_run_id": "run-rejected-write-chaos",
            "marketing_policy_approval_satisfied": True,
        },
        output={
            "external_write_state": "rejected",
            "rejection_reason": "Vendor rejected invalid audience payload.",
            "next_action": "fix_and_resubmit",
            "idempotency_key": IDEMPOTENCY_KEY,
        },
        now=NOW,
    )
    queue = build_cmo_work_queue_projection(
        external_write_results=[
            {
                **timeout_unknown,
                "connector_key": timeout_unknown["attempt"]["connector_key"],
                "workflow_id": timeout_unknown["attempt"]["workflow_id"],
                "step_id": timeout_unknown["attempt"]["step_id"],
            },
            {
                **rejected,
                "connector_key": rejected["attempt"]["connector_key"],
                "workflow_id": rejected["attempt"]["workflow_id"],
                "step_id": rejected["attempt"]["step_id"],
            },
        ],
        now=NOW,
    )
    external_write_items = [
        item for item in queue["cmo_work_queue"] if item["category"] == "external_write"
    ]

    assert timeout_unknown["step_status"] == "failed"
    assert timeout_unknown["final_state"] == "timeout_unknown"
    assert timeout_unknown["next_action"] == "manual_reconcile_before_retry"
    assert timeout_unknown["audit_events"][-1]["decision_audit"]["event_type"] == "external_write_timeout"
    assert rejected["step_status"] == "failed"
    assert rejected["final_state"] == "rejected"
    assert rejected["audit_events"][-1]["decision_audit"]["event_type"] == "external_write_rejection"
    assert all(item["severity"] == "critical" for item in external_write_items)
    assert {item["affected_connector"] for item in external_write_items} == {"google_ads"}
    assert "escalation" in {item["category"] for item in queue["cmo_work_queue"]}


def test_missing_policy_escalation_route_and_audit_evidence_block_all_production_paths() -> None:
    blocked_approval = _approval(marketing_policy_manifest_disabled=True)
    blocked_approval.pop("audit_refs")
    configs = _with_workflow(
        _base_configs(),
        "campaign_launch",
        _workflow_config(
            marketing_policy_manifest_disabled=True,
            marketing_escalation_matrix_disabled=True,
            decision_audit_disabled=True,
        ),
    )
    missing_route = evaluate_marketing_escalation(
        {
            "event_id": "evt-missing-route-chaos",
            "trigger_type": "missing_policy",
            "workflow_id": "campaign_launch",
            "severity": "high",
        },
        matrix={"marketing_escalation_matrix_disabled": True},
        now=NOW,
    )
    payload = _operating_projection(
        configs,
        approval_records=[blocked_approval],
        source_context={
            "demo": False,
            "source": "computed",
            "marketing_escalation_decisions": [missing_route],
        },
    )
    campaign = _workflow_row(payload, "campaign_launch")
    lint = lint_marketing_workflow(
        {
            "id": "wf_missing_governance_chaos",
            "domain": "marketing",
            "mode": "production",
            "steps": [
                {
                    **{
                        key: value
                        for key, value in _external_write_step().items()
                        if key != "decision_audit_required"
                    },
                    "marketing_policy_manifest_disabled": True,
                    "marketing_escalation_matrix_disabled": True,
                }
            ],
        },
        connector_contracts=[_contract_row(payload, "google_ads")],
    )
    review = payload["approval_review"]["cmo_approval_reviews"][0]
    missing_policy = evaluate_marketing_policy(
        {
            "workflow_id": "campaign_launch",
            "workflow_mode": "active",
            "action": "launch_campaign",
            "external_write_required": True,
            "marketing_policy_manifest_disabled": True,
        }
    )

    assert campaign["state"] == "promotion_blocked"
    assert campaign["marketing_policy"]["status"] == "missing_policy"
    assert campaign["escalation_matrix"]["status"] == "missing_route"
    assert campaign["decision_audit"]["status"] == "missing_audit_evidence"
    assert lint.has_errors is True
    assert {
        "marketing_policy_missing",
        "marketing_escalation_route_missing",
        "marketing_decision_audit_evidence_missing",
    } <= {finding.code for finding in lint.errors}
    assert missing_policy["decision"] == "missing_policy"
    assert missing_route["route_found"] is False
    assert review["status"] == "blocked"
    assert "approve" not in review["allowed_reviewer_actions"]
    assert {"workflow", "policy", "audit", "escalation"} <= _work_item_categories(payload)


def test_report_quality_gate_and_kpi_reconciliation_failure_block_trusted_outputs() -> None:
    payload = _operating_projection(
        _with_workflow(_base_configs(), "weekly_marketing_report"),
        source_data=_source_facts(
            campaign_spend_by_channel={"google": 400.0, "linkedin": 150.0},
            opportunities=[],
        ),
        report_types=("weekly_marketing_report",),
        production_tenant=True,
    )
    spend_check = _reconciliation(payload, "paid_spend_totals_by_channel")
    weekly_gate = _report_gate(payload, "weekly_marketing_report")

    assert spend_check["status"] == "failed"
    assert spend_check["severity"] == "high"
    assert _kpi(payload, "cac")["status"] == "blocked"
    assert _kpi(payload, "roas")["status"] == "blocked"
    assert weekly_gate["status"] == "blocked"
    assert weekly_gate["trusted_delivery_allowed"] is False
    assert weekly_gate["safe_report_mode"] == "draft_only"
    assert "paid_spend_totals_by_channel" in weekly_gate["failed_reconciliation_keys"]
    assert {"report", "reconciliation", "kpi"} <= _work_item_categories(payload)
