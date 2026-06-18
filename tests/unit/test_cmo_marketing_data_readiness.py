from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from api.v1.kpis import _apply_cmo_production_data_policy
from core.marketing.connector_setup import build_marketing_connector_setup
from core.marketing.data_readiness import build_marketing_data_readiness

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
                "granted_scopes": [
                    "crm.objects.contacts.read",
                    "crm.objects.deals.read",
                    "automation",
                ],
                "data_coverage_status": "ready",
                "marketing_field_mapping": hubspot_mapping,
                "marketing_backfill": _backfill(),
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
            },
        ),
        _connector_config(
            "mailchimp",
            config={
                "owner": "email@example.com",
                "audience_id": "aud-1",
                "marketing_field_mapping": mailchimp_mapping,
                "marketing_backfill": _backfill(),
            },
        ),
    ]


def _readiness(configs: list[SimpleNamespace]) -> dict:
    setup = build_marketing_connector_setup(configs, now=NOW)
    return build_marketing_data_readiness(setup, configs, now=NOW)


def _mapping(readiness: dict, key: str) -> dict:
    return next(row for row in readiness["field_mapping_status"] if row["key"] == key)


def _backfill_row(readiness: dict, key: str) -> dict:
    return next(row for row in readiness["backfill_status"] if row["source_connector_key"] == key)


def test_all_required_mappings_and_completed_backfills_enable_kpi_readiness() -> None:
    readiness = _readiness(_base_configs())

    assert readiness["field_mapping_summary"]["readiness"] == "ready"
    assert readiness["backfill_summary"]["readiness"] == "ready"
    assert readiness["kpi_readiness"]["status"] == "ready"
    assert readiness["kpi_readiness"]["historical_status"] == "ready"
    assert _mapping(readiness, "lifecycle_stages")["status"] == "valid"
    assert _backfill_row(readiness, "hubspot")["status"] == "completed"


def test_missing_lifecycle_stage_mapping_blocks_kpi_readiness() -> None:
    configs = _base_configs()
    del configs[0].config["marketing_field_mapping"]["lifecycle_stages"]

    readiness = _readiness(configs)

    lifecycle = _mapping(readiness, "lifecycle_stages")
    assert lifecycle["status"] == "unmapped"
    assert lifecycle["next_action_cta"] == "map_fields"
    assert readiness["kpi_readiness"]["status"] == "blocked"
    assert any("Lifecycle stages" in reason for reason in readiness["kpi_readiness"]["blocked_reasons"])


def test_missing_revenue_field_blocks_pipeline_cac_readiness() -> None:
    configs = _base_configs()
    del configs[0].config["marketing_field_mapping"]["opportunity_revenue"]

    readiness = _readiness(configs)

    revenue = _mapping(readiness, "opportunity_revenue")
    assert revenue["status"] == "unmapped"
    assert "Pipeline contribution" in revenue["affected_kpis"]
    assert "CAC" in revenue["affected_kpis"]
    assert readiness["kpi_readiness"]["status"] == "blocked"


def test_missing_consent_fields_block_email_and_nurture_readiness() -> None:
    configs = _base_configs()
    del configs[0].config["marketing_field_mapping"]["consent_unsubscribe"]
    del configs[3].config["marketing_field_mapping"]["consent_unsubscribe"]

    readiness = _readiness(configs)

    consent = _mapping(readiness, "consent_unsubscribe")
    assert consent["status"] == "unmapped"
    assert "Email performance" in consent["affected_kpis"]
    assert "Lead nurture readiness" in consent["affected_kpis"]
    assert readiness["kpi_readiness"]["status"] == "blocked"


def test_invalid_currency_and_timezone_mappings_are_rejected() -> None:
    configs = _base_configs()
    mappings = configs[0].config["marketing_field_mapping"]
    mappings["currency"] = {"currency": "ZZZ", "updated_at": "2026-05-01T00:00:00+00:00"}
    mappings["timezone"] = {"timezone": "Mars/Base", "updated_at": "2026-05-01T00:00:00+00:00"}

    readiness = _readiness(configs)

    currency = _mapping(readiness, "currency")
    timezone = _mapping(readiness, "timezone")
    assert currency["status"] == "invalid"
    assert "Currency" in currency["blocking_reason"]
    assert timezone["status"] == "invalid"
    assert "Timezone" in timezone["blocking_reason"]
    assert readiness["kpi_readiness"]["status"] == "blocked"


def test_partial_mapping_degrades_readiness_without_fake_healthy_state() -> None:
    configs = _base_configs()
    del configs[2].config["marketing_field_mapping"]["utm_fields"]

    readiness = _readiness(configs)

    utm = _mapping(readiness, "utm_fields")
    assert utm["status"] == "partially_mapped"
    assert utm["next_action_cta"] == "complete_mapping"
    assert readiness["field_mapping_summary"]["readiness"] == "degraded"
    assert readiness["kpi_readiness"]["status"] == "degraded"


def test_stale_mapping_requires_review() -> None:
    configs = _base_configs()
    stale_mapping = configs[0].config["marketing_field_mapping"]["lifecycle_stages"]
    stale_mapping["updated_at"] = "2025-01-01T00:00:00+00:00"

    readiness = _readiness(configs)

    lifecycle = _mapping(readiness, "lifecycle_stages")
    assert lifecycle["status"] == "stale"
    assert lifecycle["next_action_cta"] == "review_mapping"
    assert readiness["kpi_readiness"]["status"] == "degraded"


def test_failed_and_blocked_backfills_create_actionable_next_steps() -> None:
    configs = _base_configs()
    configs[1].config["marketing_backfill"] = _backfill("failed")
    configs[2] = _connector_config(
        "ga4",
        config=configs[2].config,
        health_status="expired_auth",
        sync_error="OAuth token expired",
    )

    readiness = _readiness(configs)

    google_ads = _backfill_row(readiness, "google_ads")
    ga4 = _backfill_row(readiness, "ga4")
    assert google_ads["status"] == "failed"
    assert google_ads["next_action_cta"] == "retry_backfill"
    assert google_ads["blocking_reason"] == "Vendor export failed"
    assert ga4["status"] == "blocked"
    assert ga4["next_action_cta"] == "resolve_blocker"
    assert readiness["backfill_summary"]["readiness"] == "blocked"
    assert readiness["kpi_readiness"]["status"] == "blocked"


def test_production_tenant_cannot_treat_blocked_readiness_as_confident_kpis() -> None:
    base = {
        "demo": False,
        "source": "computed",
        "agent_count": 5,
        "total_tasks_30d": 10,
    }
    result = _apply_cmo_production_data_policy(
        base,
        {"readiness": "ready"},
        {
            "status": "blocked",
            "blocked_reasons": ["Lifecycle stages mapping is unmapped."],
        },
        strict_runtime=True,
    )

    assert result["demo"] is False
    assert result["production_data_blocked"] is True
    assert result["kpi_confidence_status"] == "blocked"
    assert result["data_coverage_status"] == "blocked_mapping_or_backfill"
    assert "field mappings" in result["message"]


@pytest.mark.parametrize(
    ("status", "expected_cta"),
    [
        ("not_started", "start_backfill"),
        ("queued", "monitor_backfill"),
        ("running", "monitor_backfill"),
        ("partial", "review_failed_records"),
    ],
)
def test_backfill_progress_states_are_visible(status: str, expected_cta: str) -> None:
    configs = deepcopy(_base_configs())
    configs[1].config["marketing_backfill"] = _backfill(status)

    readiness = _readiness(configs)

    row = _backfill_row(readiness, "google_ads")
    assert row["status"] == status
    assert row["next_action_cta"] == expected_cta
    assert row["requested_start"] == "2025-05-01"
    assert row["records_discovered"] == 100
