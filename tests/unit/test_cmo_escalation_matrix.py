from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from core.marketing.approval_timeouts import evaluate_approval_timeout
from core.marketing.connector_retry_policy import (
    build_degraded_mode_projection,
    policy_projection,
)
from core.marketing.connector_setup import build_marketing_connector_setup
from core.marketing.data_readiness import build_marketing_data_readiness
from core.marketing.escalation_matrix import (
    ESCALATION_TRIGGER_TYPES,
    build_workflow_escalation_status,
    default_marketing_escalation_matrix,
    evaluate_marketing_escalation,
)
from core.marketing.external_writes import evaluate_marketing_external_write_result
from core.marketing.policy_manifest import evaluate_marketing_policy
from core.marketing.workflow_activation import build_cmo_workflow_activation
from core.marketing.workflow_linter import lint_marketing_workflow

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def test_default_escalation_matrix_loads_with_version_and_policy_ids() -> None:
    matrix = default_marketing_escalation_matrix()

    assert matrix["policy_id"] == "cmo_default_escalation_matrix"
    assert matrix["version"] == "2026-05-23.cmo-6.2"
    assert {route["trigger_type"] for route in matrix["routes"]} == set(ESCALATION_TRIGGER_TYPES)
    assert all(route["audit_event_code"].startswith("cmo_escalation_") for route in matrix["routes"])


def test_approval_timeout_escalates_to_configured_owner_chain_and_evidence() -> None:
    decision = evaluate_approval_timeout(
        {
            "approval_id": "apr-1",
            "workflow_id": "campaign_launch",
            "workflow_run_id": "run-1",
            "step_id": "launch",
            "action": "launch_campaign",
            "created_at": NOW - timedelta(hours=5),
            "due_at": NOW - timedelta(hours=1),
            "status": "pending",
        },
        now=NOW,
    )

    evidence = decision["escalation_evidence"]
    assert decision["timed_out"] is True
    assert decision["escalation_decision"]["trigger_type"] == "approval_timeout"
    assert decision["escalation_decision"]["decision"] == "escalate"
    assert evidence["owner_role"] == "cmo"
    assert evidence["workflow_id"] == "campaign_launch"
    assert evidence["workflow_run_id"] == "run-1"
    assert evidence["step_id"] == "launch"
    assert evidence["due_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert evidence["audit_reference"].startswith("mkt_escalation_")
    assert decision["audit_evidence"]["escalation_evidence"] == evidence


def test_crisis_public_response_escalates_to_cmo_ceo_and_legal_chain() -> None:
    decision = evaluate_marketing_escalation(
        {
            "trigger_type": "crisis_public_response",
            "workflow_id": "brand_crisis_response",
            "step_id": "public_response",
        },
        now=NOW,
    )

    assert decision["decision"] == "escalate"
    assert decision["severity"] == "critical"
    assert decision["primary_owner_role"] == "cmo"
    assert {"cmo", "ceo", "legal"}.issubset(set(decision["escalation_chain"]))


def test_major_budget_threshold_routes_to_finance_owner() -> None:
    decision = evaluate_marketing_escalation(
        {
            "trigger_type": "budget_threshold_exceeded",
            "workflow_id": "daily_spend_optimization",
            "action": "mutate_ad_budget",
        },
        now=NOW,
    )

    assert decision["decision"] == "escalate_to_finance"
    assert "cfo" in decision["escalation_chain"]
    assert decision["next_action_cta"] == "request_finance_budget_review"


def test_connector_auth_failure_routes_to_admin_or_it_owner() -> None:
    decision = evaluate_marketing_escalation(
        {
            "trigger_type": "connector_auth_expired",
            "connector_key": "google_ads",
            "failure_class": "auth_expired",
        },
        now=NOW,
    )

    assert decision["decision"] == "escalate_to_admin"
    assert decision["primary_owner_role"] == "admin_it_owner"
    assert "admin_it_owner" in decision["escalation_chain"]


def test_data_mapping_and_backfill_failures_route_to_marketing_ops_revops() -> None:
    mapping = evaluate_marketing_escalation({"trigger_type": "data_mapping_blocked"}, now=NOW)
    backfill = evaluate_marketing_escalation({"trigger_type": "backfill_failed"}, now=NOW)

    assert mapping["decision"] == "require_manual_resolution"
    assert mapping["primary_owner_role"] == "marketing_ops"
    assert "revops_lead" in mapping["escalation_chain"]
    assert backfill["decision"] == "require_manual_resolution"
    assert "admin_it_owner" in backfill["escalation_chain"]


def test_missing_policy_requires_manual_resolution_or_owner_notification() -> None:
    decision = evaluate_marketing_escalation(
        {"trigger_type": "missing_policy", "workflow_id": "campaign_launch"},
        now=NOW,
    )

    assert decision["decision"] == "require_manual_resolution"
    assert decision["primary_owner_role"] == "policy_owner"
    assert decision["fallback_outcome"] == "require_manual_resolution"


def test_external_write_rejected_routes_to_workflow_owner_and_cmo() -> None:
    decision = evaluate_marketing_external_write_result(
        [_write_contract()],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        output={
            "external_write_state": "rejected",
            "error": "Vendor rejected budget payload.",
            "idempotency_key": "launch-1",
            "approved": True,
        },
        step={"id": "launch", "idempotency_key": "launch-1"},
        state={"workflow_id": "campaign_launch", "workflow_run_id": "run-1"},
        now=NOW,
    )

    assert decision["step_status"] == "failed"
    assert decision["escalation_decision"]["trigger_type"] == "external_write_rejected"
    assert decision["escalation_evidence"]["owner_role"] == "workflow_owner"
    assert "cmo" in decision["escalation_decision"]["escalation_chain"]


def test_pricing_or_legal_claim_inserts_legal_compliance_reviewer() -> None:
    escalation = evaluate_marketing_escalation(
        {"trigger_type": "pricing_or_legal_claim", "action": "pricing_claim"},
        now=NOW,
    )
    policy = evaluate_marketing_policy(
        {
            "workflow_id": "content_pipeline",
            "workflow_mode": "active",
            "action": "publish",
            "pricing_claim": True,
        }
    )

    assert escalation["decision"] == "escalate_to_legal"
    assert escalation["primary_owner_role"] == "legal"
    assert "compliance" in escalation["escalation_chain"]
    assert policy["escalation_decision"]["trigger_type"] == "pricing_or_legal_claim"
    assert policy["escalation_evidence"]["owner_role"] == "legal"


def test_target_account_change_routes_to_cmo_and_growth_owner() -> None:
    decision = evaluate_marketing_escalation(
        {"trigger_type": "target_account_change", "workflow_id": "abm_sprint"},
        now=NOW,
    )

    assert decision["decision"] == "escalate"
    assert decision["primary_owner_role"] == "growth_lead"
    assert "cmo" in decision["escalation_chain"]


def test_connector_retry_and_data_readiness_expose_escalation_evidence() -> None:
    degraded = build_degraded_mode_projection(
        connector_key="google_ads",
        connector_name="Google Ads",
        category="Ads",
        failure_class="auth_expired",
        reason="OAuth token expired.",
        policy=policy_projection("auth_expired"),
        read_status="blocked",
        write_status="blocked",
        next_action_cta="reconnect",
    )
    readiness = build_marketing_data_readiness(
        [
            {
                "key": "hubspot",
                "name": "HubSpot",
                "category": "CRM",
                "configured_status": "configured",
                "health_status": "expired_auth",
                "detail": "OAuth token expired.",
            }
        ],
        [],
        now=NOW,
    )

    lifecycle = next(row for row in readiness["field_mapping_status"] if row["key"] == "lifecycle_stages")
    backfill = next(row for row in readiness["backfill_status"] if row["source_connector_key"] == "hubspot")
    assert degraded["escalation_decision"]["trigger_type"] == "connector_auth_expired"
    assert degraded["escalation_evidence"]["owner_role"] == "admin_it_owner"
    assert lifecycle["escalation_decision"]["trigger_type"] == "data_mapping_blocked"
    assert lifecycle["escalation_evidence"]["owner_role"] == "marketing_ops"
    assert backfill["escalation_decision"]["trigger_type"] == "backfill_failed"


def test_workflow_activation_blocks_when_required_escalation_route_is_missing() -> None:
    configs = _with_workflow(
        _base_configs(),
        "campaign_launch",
        _workflow_config(marketing_escalation_matrix_disabled=True),
    )
    setup = build_marketing_connector_setup(configs, now=NOW)
    readiness = build_marketing_data_readiness(setup, configs, now=NOW)
    activation = build_cmo_workflow_activation(setup, readiness, configs, now=NOW)

    campaign = next(
        row
        for row in activation["workflow_activation_status"]
        if row["workflow_key"] == "campaign_launch"
    )

    assert campaign["state"] == "promotion_blocked"
    assert campaign["next_action_cta"] == "configure_escalation_matrix"
    assert campaign["escalation_matrix"]["status"] == "missing_route"
    assert any("Escalation route is missing" in reason for reason in campaign["blocked_reasons"])


def test_workflow_linter_flags_escalation_sensitive_production_step_without_route() -> None:
    result = lint_marketing_workflow(
        {
            "id": "wf_campaign",
            "domain": "marketing",
            "mode": "production",
            "steps": [
                {
                    "id": "launch",
                    "type": "agent",
                    "agent_type": "campaign_pilot",
                    "action": "launch_campaign",
                    "connector_key": "google_ads",
                    "external_write_confirmation_required": True,
                    "expected_confirmation_fields": ["external_object_id"],
                    "idempotency_key": "launch-1",
                    "marketing_escalation_matrix_disabled": True,
                }
            ],
        },
        connector_contracts=[_write_contract()],
    )

    assert result.has_errors is True
    assert "marketing_escalation_route_missing" in {finding.code for finding in result.errors}


def test_escalation_status_does_not_activate_unrelated_workflows() -> None:
    campaign = build_workflow_escalation_status(
        "campaign_launch",
        ["launch_campaign"],
        {"marketing_escalation_matrix_disabled": True},
    )
    weekly = build_workflow_escalation_status(
        "weekly_marketing_report",
        [],
        {"marketing_escalation_matrix_disabled": True},
    )

    assert campaign["status"] == "missing_route"
    assert campaign["next_action_cta"] == "configure_escalation_matrix"
    assert weekly["status"] == "not_required"
    assert weekly["next_action_cta"] == "none"


def _connector_config(
    connector_name: str,
    *,
    config: dict,
    health_status: str = "healthy",
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
        last_sync_at=NOW - timedelta(hours=1),
        sync_error=None,
    )


def _backfill() -> dict:
    return {
        "status": "completed",
        "requested_start": "2025-05-01",
        "requested_end": "2026-05-23",
        "records_discovered": 100,
        "records_imported": 100,
        "records_skipped": 0,
        "records_failed": 0,
        "last_run_at": "2026-05-23T10:00:00+00:00",
    }


def _contract() -> dict:
    return {
        "idempotency_supported": True,
        "retry_budget": {
            "max_attempts": 3,
            "attempts_used": 0,
            "remaining_attempts": 3,
            "idempotency_supported": True,
            "idempotency_key": "mkt-setup-001",
        },
    }


def _write_contract() -> dict:
    return {
        "connector_key": "google_ads",
        "category": "Ads",
        "read_ready": True,
        "write_ready": True,
        "write_safe": True,
        "idempotency_key_supported": True,
        "retry_budget": {
            "idempotency_key": "launch-1",
            "idempotency_supported": True,
            "remaining_attempts": 2,
        },
    }


def _base_configs() -> list[SimpleNamespace]:
    updated_at = "2026-05-01T00:00:00+00:00"
    hubspot_mapping = {
        "lifecycle_stages": {
            "source_field": "lifecyclestage",
            "stage_map": {"mql": "marketingqualifiedlead", "sql": "salesqualifiedlead"},
            "updated_at": updated_at,
        },
        "opportunity_revenue": {
            "amount_field": "amount",
            "close_date_field": "closedate",
            "currency_field": "deal_currency_code",
            "updated_at": updated_at,
        },
        "account_domains": {"domain_field": "website", "updated_at": updated_at},
        "consent_unsubscribe": {
            "consent_field": "marketing_opt_in",
            "unsubscribe_field": "unsubscribed",
            "updated_at": updated_at,
        },
        "fiscal_calendar": {"fiscal_year_start_month": 4, "updated_at": updated_at},
        "currency": {"currency": "USD", "updated_at": updated_at},
        "timezone": {"timezone": "Asia/Kolkata", "updated_at": updated_at},
    }
    campaign_mapping = {
        "campaign_ids": {"campaign_id_field": "campaign_id", "updated_at": updated_at},
        "utm_fields": {
            "source": "utm_source",
            "medium": "utm_medium",
            "campaign": "utm_campaign",
            "updated_at": updated_at,
        },
    }
    mailchimp_mapping = {
        **campaign_mapping,
        "consent_unsubscribe": {
            "consent_field": "marketing_permission",
            "unsubscribe_field": "unsubscribe_status",
            "updated_at": updated_at,
        },
    }
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
                "marketing_field_mapping": hubspot_mapping,
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        ),
        _connector_config(
            "google_ads",
            config={
                "owner": "growth@example.com",
                "customer_id": "123-456-7890",
                "granted_scopes": ["https://www.googleapis.com/auth/adwords"],
                "marketing_field_mapping": campaign_mapping,
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        ),
        _connector_config(
            "ga4",
            config={
                "owner": "analytics@example.com",
                "property_id": "properties/123",
                "granted_scopes": ["https://www.googleapis.com/auth/analytics.readonly"],
                "marketing_field_mapping": campaign_mapping,
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": {"retry_budget": {"remaining_attempts": 3}},
            },
        ),
        _connector_config(
            "mailchimp",
            config={
                "owner": "email@example.com",
                "audience_id": "aud-1",
                "marketing_field_mapping": mailchimp_mapping,
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        ),
    ]


def _workflow_config(**overrides: object) -> dict:
    payload = {
        "mode": "shadow",
        "promoted": False,
        "approval_owner": "cmo@example.com",
        "policy_owner": "legal@example.com",
        "shadow_quality": {
            "status": "passed",
            "sample_count": 5,
            "success_rate": 0.95,
            "last_run_at": "2026-05-23T09:00:00+00:00",
        },
    }
    payload.update(overrides)
    return payload


def _with_workflow(
    configs: list[SimpleNamespace],
    workflow_key: str,
    payload: dict,
) -> list[SimpleNamespace]:
    configs = deepcopy(configs)
    workflows = configs[0].config.setdefault("marketing_workflows", {})
    workflows[workflow_key] = payload
    return configs
