from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from core.marketing.approval_timeouts import evaluate_approval_timeout
from core.marketing.connector_setup import build_marketing_connector_setup
from core.marketing.data_readiness import build_marketing_data_readiness
from core.marketing.external_writes import evaluate_marketing_external_write_result
from core.marketing.policy_manifest import (
    default_marketing_policy_manifest,
    evaluate_marketing_policy,
)
from core.marketing.workflow_activation import build_cmo_workflow_activation
from core.marketing.workflow_linter import lint_marketing_workflow

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def test_default_manifest_loads_with_version_and_policy_ids() -> None:
    manifest = default_marketing_policy_manifest()

    assert manifest["policy_id"] == "cmo_default_marketing_policy"
    assert manifest["version"] == "2026-05-23.cmo-6.1"
    assert manifest["rules"]
    assert {rule["rule_id"] for rule in manifest["rules"]} >= {
        "cmo_publish_send_launch_approval",
        "cmo_forbidden_destructive_actions",
        "cmo_read_only_autonomy",
    }


def test_publish_send_and_launch_actions_require_approval() -> None:
    for action in ("publish", "send_email", "launch_campaign"):
        decision = evaluate_marketing_policy(
            {
                "workflow_id": "campaign_launch",
                "workflow_mode": "active",
                "action": action,
                "external_write_required": True,
            }
        )

        assert decision["decision"] == "requires_approval"
        assert decision["required_approver_role"] == "cmo"
        assert decision["required_audit_evidence"]


def test_budget_increase_above_threshold_requires_approval() -> None:
    decision = evaluate_marketing_policy(
        {
            "workflow_id": "daily_spend_optimization",
            "workflow_mode": "active",
            "action": "mutate_ad_budget",
            "channel": "google_ads",
            "budget_delta": 1500,
        }
    )

    assert decision["decision"] == "requires_approval"
    assert decision["matched_rules"][0]["rule_id"] == "cmo_budget_threshold_approval"
    assert decision["next_action_cta"] == "request_budget_approval"


def test_low_risk_read_only_recommendation_is_allowed_in_shadow_mode() -> None:
    decision = evaluate_marketing_policy(
        {
            "workflow_id": "weekly_marketing_report",
            "workflow_mode": "shadow",
            "action": "recommend",
        }
    )

    assert decision["decision"] == "allowed"
    assert decision["next_action_cta"] == "none"


def test_pricing_legal_and_comparative_claims_require_escalation() -> None:
    for field in ("pricing_claim", "legal_claim", "comparative_claim"):
        decision = evaluate_marketing_policy(
            {
                "workflow_id": "content_pipeline",
                "workflow_mode": "active",
                "action": "publish",
                field: True,
            }
        )

        assert decision["decision"] == "requires_escalation"
        assert decision["required_escalation_role"] == "legal"


def test_crisis_public_response_requires_escalation() -> None:
    decision = evaluate_marketing_policy(
        {
            "workflow_id": "brand_crisis_response",
            "workflow_mode": "active",
            "action": "public_response",
            "public_response": True,
        }
    )

    assert decision["decision"] == "requires_escalation"
    assert decision["required_escalation_role"] == "ceo"


def test_target_account_list_change_above_threshold_requires_approval() -> None:
    decision = evaluate_marketing_policy(
        {
            "workflow_id": "abm_sprint",
            "workflow_mode": "active",
            "action": "update_target_accounts",
            "target_account_delta": 40,
        }
    )

    assert decision["decision"] == "requires_approval"
    assert decision["required_approver_role"] == "revops_lead"


def test_disallowed_action_is_blocked() -> None:
    decision = evaluate_marketing_policy(
        {
            "workflow_id": "campaign_launch",
            "workflow_mode": "active",
            "action": "delete_campaign",
            "external_write_required": True,
        }
    )

    assert decision["decision"] == "blocked"
    assert decision["next_action_cta"] == "remove_destructive_action"


def test_missing_policy_fails_closed_for_active_external_write() -> None:
    decision = evaluate_marketing_policy(
        {
            "workflow_id": "campaign_launch",
            "workflow_mode": "active",
            "action": "launch_campaign",
            "external_write_required": True,
            "marketing_policy_manifest_disabled": True,
        }
    )

    assert decision["decision"] == "missing_policy"
    assert decision["next_action_cta"] == "configure_marketing_policy_manifest"


def test_missing_policy_can_continue_only_as_shadow_read_only_recommendation() -> None:
    decision = evaluate_marketing_policy(
        {
            "workflow_id": "weekly_marketing_report",
            "workflow_mode": "shadow",
            "action": "recommend",
            "marketing_policy_manifest_disabled": True,
        }
    )

    assert decision["decision"] == "read_only_only"
    assert decision["next_action_cta"] == "label_as_read_only_recommendation"


def test_workflow_activation_blocks_active_workflow_missing_required_policy() -> None:
    configs = _with_workflow(
        _base_configs(),
        "campaign_launch",
        _workflow_config(marketing_policy_manifest_disabled=True),
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
    assert campaign["next_action_cta"] == "configure_marketing_policy_manifest"
    assert campaign["marketing_policy"]["status"] == "missing_policy"


def test_workflow_linter_flags_production_step_missing_policy() -> None:
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
                    "marketing_policy_manifest_disabled": True,
                }
            ],
        },
        connector_contracts=[_write_contract()],
    )

    assert result.has_errors is True
    assert "marketing_policy_missing" in {finding.code for finding in result.errors}


def test_external_write_completion_refuses_customer_write_without_policy_approval() -> None:
    decision = evaluate_marketing_external_write_result(
        [_write_contract()],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        step={"id": "launch", "idempotency_key": "launch-1"},
        state={"workflow_id": "campaign_launch", "workflow_mode": "active"},
        output={
            "external_write_state": "accepted",
            "external_object_id": "customers/123/campaigns/456",
            "idempotency_key": "launch-1",
        },
        now=NOW,
    )

    assert decision["step_status"] == "failed"
    assert decision["error_code"] == "external_write_marketing_policy_approval_required"
    assert decision["marketing_policy_decision"]["decision"] == "requires_approval"


def test_approval_timeout_references_required_policy_role() -> None:
    decision = evaluate_approval_timeout(
        {
            "approval_id": "apr-1",
            "action": "pricing_claim",
            "status": "pending",
            "created_at": NOW - timedelta(hours=4),
            "due_at": NOW - timedelta(hours=1),
        },
        now=NOW,
    )

    assert decision["timed_out"] is True
    assert decision["policy_required_role"] == "legal"
    assert decision["audit_evidence"]["policy_required_role"] == "legal"


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
                "marketing_connector_contract": _contract(),
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
    configs = [SimpleNamespace(**vars(config)) for config in configs]
    configs[0].config = dict(configs[0].config)
    workflows = configs[0].config.setdefault("marketing_workflows", {})
    workflows[workflow_key] = payload
    return configs
