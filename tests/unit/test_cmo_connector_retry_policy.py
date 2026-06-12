from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from core.marketing.connector_contracts import build_marketing_connector_contracts
from core.marketing.connector_retry_policy import (
    CONNECTOR_FAILURE_CLASSES,
    policy_projection,
)
from core.marketing.connector_setup import build_marketing_connector_setup
from core.marketing.data_readiness import build_marketing_data_readiness
from core.marketing.external_writes import evaluate_marketing_external_write_result
from core.marketing.workflow_activation import build_cmo_workflow_activation
from core.marketing.workflow_linter import lint_marketing_workflow

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def _connector_config(
    connector_name: str,
    *,
    config: dict,
    health_status: str = "healthy",
    last_sync_at: datetime | None = None,
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
        sync_error=None,
    )


def _backfill(status: str = "completed") -> dict:
    return {
        "status": status,
        "requested_start": "2025-05-01",
        "requested_end": "2026-05-23",
        "records_discovered": 100,
        "records_imported": 100 if status == "completed" else 50,
        "records_skipped": 0,
        "records_failed": 0 if status == "completed" else 50,
        "last_run_at": "2026-05-23T10:00:00+00:00",
    }


def _contract(**overrides: object) -> dict:
    base = {
        "idempotency_supported": True,
        "retry_budget": {
            "max_attempts": 3,
            "attempts_used": 1,
            "remaining_attempts": 2,
            "next_retry_at": "2026-05-23T12:05:00+00:00",
            "idempotency_supported": True,
            "idempotency_key": "mkt-write-001",
        },
    }
    base.update(overrides)
    return base


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
                "data_coverage_status": "ready",
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
                "marketing_connector_contract": _contract(write_capabilities=[], required_write_scopes=[]),
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


def _with_workflow(configs: list[SimpleNamespace], workflow_key: str) -> list[SimpleNamespace]:
    configs = deepcopy(configs)
    configs[0].config.setdefault("marketing_workflows", {})[workflow_key] = {
        "mode": "shadow",
        "approval_owner": "cmo@example.com",
        "policy_owner": "legal@example.com",
        "shadow_quality": {
            "status": "passed",
            "sample_count": 5,
            "success_rate": 0.95,
            "last_run_at": "2026-05-23T09:00:00+00:00",
        },
    }
    return configs


def _contracts(configs: list[SimpleNamespace]) -> list[dict]:
    setup = build_marketing_connector_setup(configs, now=NOW)
    return build_marketing_connector_contracts(setup, configs, now=NOW)


def _row(rows: list[dict], connector_key: str) -> dict:
    return next(row for row in rows if row["connector_key"] == connector_key)


def test_every_failure_class_has_complete_policy_metadata() -> None:
    for failure_class in CONNECTOR_FAILURE_CLASSES:
        policy = policy_projection(failure_class)

        assert policy["failure_class"] == failure_class
        assert isinstance(policy["retryable"], bool)
        assert isinstance(policy["max_attempts"], int)
        assert policy["backoff_strategy"]["type"]
        assert isinstance(policy["safe_retry_requires_idempotency"], bool)
        assert isinstance(policy["degraded_mode_allowed"], bool)
        assert 0 <= policy["confidence_impact"] <= 1
        assert isinstance(policy["blocks_external_writes"], bool)
        assert isinstance(policy["blocks_production_kpi_confidence"], bool)
        assert policy["required_cta"] != "none"
        assert policy["audit_event_code"].startswith("cmo_connector_")


@pytest.mark.parametrize(
    ("state", "expected_cta", "expected_attempts"),
    [
        ("timeout", "review_retry_budget", 3),
        ("rate_limited", "review_retry_budget", 5),
        ("vendor_5xx", "monitor_vendor_recovery", 3),
        ("quota_exhausted", "wait_for_quota_reset", 1),
    ],
)
def test_retryable_failures_project_backoff_and_degraded_read_only_mode(
    state: str,
    expected_cta: str,
    expected_attempts: int,
) -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(
        state=state,
        retry_budget={"attempts_used": 0, "idempotency_supported": True},
    )

    google_ads = _row(_contracts(configs), "google_ads")

    assert google_ads["failure_class"] == state
    assert google_ads["read_status"] == "degraded"
    assert google_ads["write_safe"] is False
    assert google_ads["retry_policy"]["retryable"] is True
    assert google_ads["retry_budget"]["max_attempts"] == expected_attempts
    assert google_ads["retry_budget"]["backoff_strategy"]["type"] != "none"
    assert google_ads["degraded_mode"]["allowed"] is True
    assert google_ads["degraded_mode"]["confidence_impact"] > 0
    assert google_ads["next_action_cta"] == expected_cta


@pytest.mark.parametrize(
    ("state", "expected_cta"),
    [
        ("auth_expired", "reconnect"),
        ("malformed_payload", "fix_connector_payload"),
        ("connector_disabled", "enable_connector"),
    ],
)
def test_non_retryable_blocking_failures_are_not_healthy(
    state: str,
    expected_cta: str,
) -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(state=state)

    google_ads = _row(_contracts(configs), "google_ads")

    assert google_ads["failure_class"] == state
    assert google_ads["read_ready"] is False
    assert google_ads["write_safe"] is False
    assert google_ads["retry_policy"]["retryable"] is False
    assert google_ads["degraded_mode"]["status"] == "blocked"
    assert google_ads["blocks_production_kpi_confidence"] is True
    assert google_ads["next_action_cta"] == expected_cta


def test_insufficient_scope_blocks_affected_write_capability_with_setup_cta() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(
        required_write_scopes=["ads.write"],
        write_capabilities=["campaigns.mutate"],
    )

    google_ads = _row(_contracts(configs), "google_ads")

    assert google_ads["failure_class"] == "insufficient_scope"
    assert google_ads["read_ready"] is True
    assert google_ads["write_safe"] is False
    assert google_ads["write_status"] == "missing_scope"
    assert google_ads["next_action_cta"] == "add_scope"
    assert google_ads["degraded_mode"]["affected_capability"] == "Ads"


def test_partial_data_downgrades_confidence_and_marks_kpis_and_workflows() -> None:
    configs = _with_workflow(_base_configs(), "weekly_marketing_report")
    configs[1].config["marketing_connector_contract"] = _contract(state="partial_data")
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
    weekly = next(
        row
        for row in activation["workflow_activation_status"]
        if row["workflow_key"] == "weekly_marketing_report"
    )

    assert readiness["kpi_readiness"]["status"] == "degraded"
    assert "ROAS" in readiness["kpi_readiness"]["degraded_mode"]["affected_kpis"]
    assert weekly["state"] == "degraded"
    assert weekly["degraded_mode"]["confidence_impact"] >= 0.45
    assert "weekly_marketing_report" in weekly["degraded_mode"]["affected_workflows"]


def test_stale_data_is_labeled_degraded_not_fresh_production_confidence() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(
        ttl_seconds=60,
        last_sync_at="2026-05-23T10:00:00+00:00",
    )
    setup = build_marketing_connector_setup(configs, now=NOW)
    contracts = build_marketing_connector_contracts(setup, configs, now=NOW)
    readiness = build_marketing_data_readiness(
        setup,
        configs,
        connector_contracts=contracts,
        now=NOW,
    )
    google_ads = _row(contracts, "google_ads")

    assert google_ads["failure_class"] == "stale_data"
    assert google_ads["data_freshness"]["status"] == "stale"
    assert google_ads["degraded_mode"]["status"] == "degraded"
    assert readiness["kpi_readiness"]["status"] == "degraded"
    assert readiness["kpi_readiness"]["degraded_mode"]["active"] is True


def test_connector_disabled_blocks_dependent_workflow() -> None:
    configs = _with_workflow(_base_configs(), "campaign_launch")
    configs[1].config["marketing_connector_contract"] = _contract(state="connector_disabled")
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
    campaign = next(
        row
        for row in activation["workflow_activation_status"]
        if row["workflow_key"] == "campaign_launch"
    )

    assert campaign["state"] == "promotion_blocked"
    assert any("Ads connector" in reason for reason in campaign["blocked_reasons"])


def test_external_write_fails_closed_for_non_write_safe_connector_state() -> None:
    decision = evaluate_marketing_external_write_result(
        [
            {
                "connector_key": "google_ads",
                "write_safe": False,
                "blocks_external_writes": True,
                "failure_class": "rate_limited",
                "next_action_cta": "respect_rate_limit",
                "degraded_mode": {
                    "reason": "Connector is rate limited.",
                    "next_action_cta": "respect_rate_limit",
                },
            }
        ],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="active",
        output={
            "external_write_state": "accepted",
            "external_object_id": "customers/123/campaigns/456",
            "idempotency_key": "ads-launch-1",
        },
        step={"id": "launch_ads", "idempotency_key": "ads-launch-1"},
        state={"workflow_id": "campaign_launch", "workflow_run_id": "run-1"},
        now=NOW,
    )

    assert decision["step_status"] == "failed"
    assert decision["error_code"] == "external_write_connector_not_write_safe"
    assert decision["next_action"] == "respect_rate_limit"


def test_shadow_read_only_recommendation_can_continue_in_degraded_mode() -> None:
    decision = evaluate_marketing_external_write_result(
        [
            {
                "connector_key": "google_ads",
                "write_safe": False,
                "read_status": "degraded",
                "failure_class": "partial_data",
            }
        ],
        connector_key="google_ads",
        action="launch_campaign",
        workflow_mode="shadow",
        output={"recommendation": "Keep campaign in draft until connector recovers."},
        step={"id": "launch_ads"},
        state={"workflow_id": "campaign_launch", "workflow_run_id": "run-1"},
        now=NOW,
    )

    assert decision["step_status"] == "completed"
    assert decision["final_state"] == "shadow_only"


def test_linter_flags_production_dependency_on_degraded_only_connector() -> None:
    result = lint_marketing_workflow(
        {
            "id": "weekly_marketing_report",
            "domain": "marketing",
            "mode": "production",
            "steps": [
                {
                    "id": "compile_report",
                    "type": "agent",
                    "agent_type": "campaign_pilot",
                    "action": "create_plan",
                    "connector_key": "google_ads",
                }
            ],
        },
        connector_contracts=[
            {
                "connector_key": "google_ads",
                "category": "Ads",
                "read_ready": False,
                "read_status": "degraded",
                "degraded_mode": {"status": "degraded", "allowed": True},
            }
        ],
    )

    assert result.has_errors is True
    assert "marketing_connector_degraded_only" in {finding.code for finding in result.findings}
