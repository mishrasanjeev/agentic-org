from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from core.marketing.connector_contracts import (
    build_marketing_connector_contracts,
    evaluate_marketing_write_completion,
    plan_marketing_write_retry,
    summarize_marketing_connector_contracts,
)
from core.marketing.connector_setup import build_marketing_connector_setup
from core.marketing.data_readiness import build_marketing_data_readiness
from core.marketing.workflow_activation import build_cmo_workflow_activation

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def _connector_config(
    connector_name: str,
    *,
    config: dict,
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


def _backfill(status: str = "completed") -> dict:
    return {
        "status": status,
        "requested_start": "2025-05-01",
        "requested_end": "2026-05-23",
        "records_discovered": 100,
        "records_imported": 98 if status in {"completed", "partial"} else 0,
        "records_skipped": 1,
        "records_failed": 1 if status == "partial" else 0,
        "last_run_at": "2026-05-23T10:00:00+00:00",
        "blocking_reason": "Vendor export failed" if status in {"failed", "blocked"} else None,
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
        "account_domains": {
            "domain_field": "website",
            "updated_at": updated_at,
        },
        "consent_unsubscribe": {
            "consent_field": "marketing_opt_in",
            "unsubscribe_field": "unsubscribed",
            "updated_at": updated_at,
        },
        "fiscal_calendar": {
            "fiscal_year_start_month": 4,
            "updated_at": updated_at,
        },
        "currency": {
            "currency": "USD",
            "updated_at": updated_at,
        },
        "timezone": {
            "timezone": "Asia/Kolkata",
            "updated_at": updated_at,
        },
    }
    campaign_mapping = {
        "campaign_ids": {
            "campaign_id_field": "campaign_id",
            "updated_at": updated_at,
        },
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
                "source_object_id": "crm-list-7",
                "source_url": "https://app.hubspot.com/lists/7",
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
                "marketing_connector_contract": {
                    "retry_budget": {
                        "max_attempts": 3,
                        "attempts_used": 0,
                        "remaining_attempts": 3,
                    },
                },
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


def _contracts(configs: list[SimpleNamespace]) -> list[dict]:
    setup = build_marketing_connector_setup(configs, now=NOW)
    return build_marketing_connector_contracts(setup, configs, now=NOW)


def _contract_row(rows: list[dict], connector_key: str) -> dict:
    return next(row for row in rows if row["connector_key"] == connector_key)


def _workflow_config() -> dict:
    return {
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


def _campaign_workflow(configs: list[SimpleNamespace]) -> dict:
    configs = deepcopy(configs)
    configs[0].config.setdefault("marketing_workflows", {})["campaign_launch"] = _workflow_config()
    setup = build_marketing_connector_setup(configs, now=NOW)
    readiness = build_marketing_data_readiness(setup, configs, now=NOW)
    contracts = build_marketing_connector_contracts(setup, configs, now=NOW)
    activation = build_cmo_workflow_activation(
        setup,
        readiness,
        configs,
        connector_contracts=contracts,
        now=NOW,
    )
    return next(row for row in activation["workflow_activation_status"] if row["workflow_key"] == "campaign_launch")


def test_read_only_connector_is_not_write_ready() -> None:
    configs = _base_configs()
    configs[0].config["marketing_connector_contract"] = _contract(
        write_capabilities=[],
        required_write_scopes=[],
    )

    hubspot = _contract_row(_contracts(configs), "hubspot")

    assert hubspot["read_ready"] is True
    assert hubspot["write_ready"] is False
    assert hubspot["write_status"] == "read_only"
    assert hubspot["source_objects"][0]["source_object_id"] == "crm-list-7"


def test_hubspot_private_app_without_persisted_scopes_is_read_ready() -> None:
    configs = _base_configs()
    configs[0].config.pop("granted_scopes")

    hubspot = _contract_row(_contracts(configs), "hubspot")

    assert hubspot["read_ready"] is True
    assert hubspot["read_status"] == "ready"
    assert hubspot["missing_read_scopes"] == []
    assert hubspot["missing_write_scopes"] == ["automation"]
    assert hubspot["write_status"] == "missing_scope"
    assert hubspot["read_scope_evidence"] == [
        "Healthy HubSpot connector state proves CRM read capability even without a persisted OAuth scope string."
    ]


def test_missing_write_scope_blocks_write_workflow_readiness() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(
        required_write_scopes=["ads.write"],
        write_capabilities=["campaigns.mutate"],
    )

    google_ads = _contract_row(_contracts(configs), "google_ads")
    campaign = _campaign_workflow(configs)

    assert google_ads["read_ready"] is True
    assert google_ads["write_ready"] is False
    assert google_ads["missing_write_scopes"] == ["ads.write"]
    assert campaign["state"] == "promotion_blocked"
    assert any("not write-ready" in reason for reason in campaign["blocked_reasons"])


def test_missing_write_scope_state_does_not_block_read_contract() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(
        state="missing_scope",
        required_write_scopes=["ads.write"],
        write_capabilities=["campaigns.mutate"],
    )

    google_ads = _contract_row(_contracts(configs), "google_ads")

    assert google_ads["read_ready"] is True
    assert google_ads["read_status"] == "ready"
    assert google_ads["write_ready"] is False
    assert google_ads["write_status"] == "missing_scope"
    assert google_ads["missing_read_scopes"] == []
    assert google_ads["missing_write_scopes"] == ["ads.write"]


def test_expired_auth_blocks_connector_contract_readiness() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(auth_status="expired")

    google_ads = _contract_row(_contracts(configs), "google_ads")

    assert google_ads["contract_state"] == "auth_expired"
    assert google_ads["auth_status"] == "expired"
    assert google_ads["read_ready"] is False
    assert google_ads["write_ready"] is False
    assert google_ads["next_action_cta"] == "reconnect"


def test_rate_limit_projects_degraded_state_and_retry_metadata() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(
        state="rate_limited",
        retry_budget={
            "max_attempts": 5,
            "attempts_used": 2,
            "remaining_attempts": 3,
            "next_retry_at": "2026-05-23T12:15:00+00:00",
            "last_error": "429 rate_limited",
            "idempotency_supported": True,
            "idempotency_key": "ads-budget-1",
        },
    )

    google_ads = _contract_row(_contracts(configs), "google_ads")

    assert google_ads["contract_state"] == "rate_limited"
    assert google_ads["read_status"] == "degraded"
    assert google_ads["retry_budget"]["remaining_attempts"] == 3
    assert google_ads["retry_budget"]["next_retry_at"] == "2026-05-23T12:15:00+00:00"
    assert google_ads["next_action_cta"] == "review_retry_budget"


def test_timeout_and_vendor_5xx_project_degraded_states() -> None:
    timeout_configs = _base_configs()
    timeout_configs[1].config["marketing_connector_contract"] = _contract(state="timeout")
    timeout_row = _contract_row(_contracts(timeout_configs), "google_ads")

    vendor_configs = _base_configs()
    vendor_configs[1].config["marketing_connector_contract"] = _contract(
        state="vendor_5xx",
        degraded_mode_reason="Google Ads API returned 503",
    )
    vendor_row = _contract_row(_contracts(vendor_configs), "google_ads")

    assert timeout_row["contract_state"] == "timeout"
    assert timeout_row["read_status"] == "degraded"
    assert "timed out" in timeout_row["degraded_mode_reason"]
    assert vendor_row["contract_state"] == "vendor_5xx"
    assert vendor_row["read_status"] == "degraded"
    assert vendor_row["degraded_mode_reason"] == "Google Ads API returned 503"


def test_partial_data_downgrades_kpi_readiness() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(state="partial_data")
    setup = build_marketing_connector_setup(configs, now=NOW)
    readiness = build_marketing_data_readiness(setup, configs, now=NOW)
    google_ads = _contract_row(_contracts(configs), "google_ads")

    assert google_ads["contract_state"] == "partial_data"
    assert readiness["kpi_readiness"]["status"] == "degraded"
    assert any(
        "Google Ads connector is degraded" in reason
        for reason in readiness["kpi_readiness"]["degraded_reasons"]
    )


def test_stale_data_downgrades_kpi_readiness() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(
        ttl_seconds=60,
        last_sync_at="2026-05-23T10:00:00+00:00",
    )
    setup = build_marketing_connector_setup(configs, now=NOW)
    readiness = build_marketing_data_readiness(setup, configs, now=NOW)
    google_ads = _contract_row(_contracts(configs), "google_ads")

    assert google_ads["contract_state"] == "stale_data"
    assert google_ads["data_freshness"]["status"] == "stale"
    assert readiness["kpi_readiness"]["status"] == "degraded"
    assert any("Google Ads connector is stale" in reason for reason in readiness["kpi_readiness"]["degraded_reasons"])


def test_write_without_confirmation_cannot_be_marked_complete() -> None:
    configs = _base_configs()
    contracts = _contracts(configs)

    result = evaluate_marketing_write_completion(
        contracts,
        "google_ads",
        "launch_campaign",
        workflow_mode="active",
        idempotency_key="ads-launch-1",
    )

    assert result["can_mark_complete"] is False
    assert result["status"] == "write_unconfirmed"
    assert "confirmation is missing" in result["reason"]


def test_confirmed_write_can_be_marked_complete() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(
        write_confirmations={
            "launch_campaign": {
                "status": "write_confirmed",
                "idempotency_key": "ads-launch-1",
                "external_object_id": "customers/123/campaigns/456",
                "source_url": "https://ads.google.com/campaigns/456",
                "confirmed_at": "2026-05-23T12:02:00+00:00",
            }
        }
    )
    contracts = _contracts(configs)

    result = evaluate_marketing_write_completion(
        contracts,
        "google_ads",
        "launch_campaign",
        workflow_mode="active",
        idempotency_key="ads-launch-1",
    )

    assert result["can_mark_complete"] is True
    assert result["status"] == "write_confirmed"
    assert result["external_object_id"] == "customers/123/campaigns/456"


def test_shadow_write_step_can_remain_internal_without_external_confirmation() -> None:
    result = evaluate_marketing_write_completion(
        _contracts(_base_configs()),
        "google_ads",
        "launch_campaign",
        workflow_mode="shadow",
    )

    assert result["can_mark_complete"] is True
    assert result["status"] == "internal_only"


def test_duplicate_retry_uses_idempotency_metadata() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(
        retry_budget={
            "max_attempts": 3,
            "attempts_used": 1,
            "remaining_attempts": 2,
            "next_retry_at": "2026-05-23T12:05:00+00:00",
            "idempotency_supported": True,
            "idempotency_key": "ads-launch-1",
        },
        write_confirmations={
            "launch_campaign": {
                "status": "write_unconfirmed",
                "idempotency_key": "ads-launch-1",
            }
        },
    )

    retry = plan_marketing_write_retry(
        _contracts(configs),
        "google_ads",
        "launch_campaign",
        idempotency_key="ads-launch-1",
    )

    assert retry["safe_to_retry"] is True
    assert retry["duplicate_policy"] == "reuse_idempotency_key"
    assert retry["remaining_attempts"] == 2


def test_production_readiness_cannot_be_satisfied_by_mock_only_contract() -> None:
    configs = _base_configs()
    configs[1].config["marketing_connector_contract"] = _contract(
        production_proof="test_double",
    )
    contracts = _contracts(configs)
    google_ads = _contract_row(contracts, "google_ads")
    summary = summarize_marketing_connector_contracts(contracts)
    campaign = _campaign_workflow(configs)

    assert google_ads["mock_or_test_double"] is True
    assert google_ads["production_ready"] is False
    assert google_ads["read_ready"] is False
    assert summary["mock_or_test_double"] == 1
    assert campaign["state"] == "promotion_blocked"
    assert any("not read-ready" in reason for reason in campaign["blocked_reasons"])


def _patch_workflow_agent(monkeypatch: pytest.MonkeyPatch, output: dict) -> None:
    from core.agents.registry import AgentRegistry
    from core.schemas.messages import TaskResult
    from workflows import step_types

    monkeypatch.setattr(step_types.settings, "env", "production")
    monkeypatch.setattr(step_types.external_keys, "google_gemini_api_key", "test-key")
    monkeypatch.setattr(step_types, "_llm_available_for_workflow", lambda: True)

    class FakeAgent:
        async def execute(self, task):
            return TaskResult(
                message_id="msg-test",
                correlation_id=task.correlation_id,
                workflow_run_id=task.workflow_run_id,
                step_id=task.step_id,
                agent_id=task.target_agent.agent_id,
                status="completed",
                output=output,
                confidence=0.95,
            )

    monkeypatch.setattr(
        AgentRegistry,
        "create_from_config",
        staticmethod(lambda config: FakeAgent()),
    )


@pytest.mark.asyncio
async def test_marketing_write_agent_step_without_confirmation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workflows.step_types import execute_step

    _patch_workflow_agent(monkeypatch, {"campaign_id": "draft-1"})

    result = await execute_step(
        {
            "id": "launch_ads",
            "type": "agent",
            "agent_type": "campaign_pilot",
            "action": "launch_campaign",
            "connector_key": "google_ads",
            "external_write_required": True,
            "marketing_policy_approval_satisfied": True,
        },
        {"domain": "marketing", "workflow_mode": "active"},
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "external_write_confirmation_missing"


@pytest.mark.asyncio
async def test_marketing_write_agent_step_with_confirmation_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workflows.step_types import execute_step

    _patch_workflow_agent(
        monkeypatch,
        {
            "campaign_id": "customers/123/campaigns/456",
            "external_write_confirmation": {
                "action": "launch_campaign",
                "status": "write_confirmed",
                "idempotency_key": "ads-launch-1",
                "external_object_id": "customers/123/campaigns/456",
            },
        },
    )

    result = await execute_step(
        {
            "id": "launch_ads",
            "type": "agent",
            "agent_type": "campaign_pilot",
            "action": "launch_campaign",
            "connector_key": "google_ads",
            "external_write_required": True,
            "marketing_policy_approval_satisfied": True,
        },
        {"domain": "marketing", "workflow_mode": "active"},
    )

    assert result["status"] == "completed"
    assert result["output"]["campaign_id"] == "customers/123/campaigns/456"


@pytest.mark.asyncio
async def test_marketing_shadow_write_agent_step_can_remain_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workflows.step_types import execute_step

    _patch_workflow_agent(monkeypatch, {"recommendation": "launch later"})

    result = await execute_step(
        {
            "id": "launch_ads",
            "type": "agent",
            "agent_type": "campaign_pilot",
            "action": "launch_campaign",
            "connector_key": "google_ads",
            "external_write_required": True,
        },
        {"domain": "marketing", "workflow_mode": "shadow"},
    )

    assert result["status"] == "completed"
    assert result["output"]["recommendation"] == "launch later"
