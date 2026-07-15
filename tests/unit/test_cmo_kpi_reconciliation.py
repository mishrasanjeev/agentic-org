from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.v1 import kpis as kpis_api
from core.marketing.kpi_schema import build_unified_cmo_kpi_projection

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
FRESH_TS = (NOW - timedelta(hours=1)).isoformat()

ALL_CONNECTOR_CATEGORIES = (
    "CRM",
    "Ads",
    "Analytics",
    "CMS",
    "Email",
    "Brand",
    "ABM",
    "Finance",
)
ALL_MAPPING_KEYS = (
    "lifecycle_stages",
    "opportunity_revenue",
    "campaign_ids",
    "utm_fields",
    "account_domains",
    "consent_unsubscribe",
    "fiscal_calendar",
    "currency",
    "timezone",
)


def _setup_rows(
    *,
    categories: tuple[str, ...] = ALL_CONNECTOR_CATEGORIES,
    health_overrides: dict[str, str] | None = None,
) -> list[dict]:
    health_overrides = health_overrides or {}
    return [
        {
            "key": category.lower(),
            "name": f"{category} connector",
            "category": category,
            "configured_status": "configured",
            "health_status": health_overrides.get(category, "healthy"),
            "last_sync_at": FRESH_TS,
        }
        for category in categories
    ]


def _contract_rows(
    *,
    categories: tuple[str, ...] = ALL_CONNECTOR_CATEGORIES,
    failure_overrides: dict[str, str] | None = None,
) -> list[dict]:
    failure_overrides = failure_overrides or {}
    rows = []
    for category in categories:
        failure = failure_overrides.get(category)
        rows.append(
            {
                "connector_key": category.lower(),
                "category": category,
                "configured_status": "configured",
                "read_ready": True,
                "read_status": "ready",
                "failure_class": failure,
                "blocks_production_kpi_confidence": False,
                "degraded_mode": {
                    "status": "degraded" if failure == "partial_data" else "none",
                    "confidence_impact": 0.15 if failure == "partial_data" else 0.0,
                },
            }
        )
    return rows


def _data_readiness(categories: tuple[str, ...] = ALL_CONNECTOR_CATEGORIES) -> dict:
    return {
        "field_mapping_status": [
            {
                "key": key,
                "name": key.replace("_", " ").title(),
                "status": "valid",
                "decision_audit_ref": f"audit-mapping-{key}",
            }
            for key in ALL_MAPPING_KEYS
        ],
        "backfill_status": [
            {
                "source_connector_key": category.lower(),
                "source_name": f"{category} connector",
                "category": category,
                "status": "completed",
                "decision_audit_ref": f"audit-backfill-{category.lower()}",
            }
            for category in categories
        ],
        "kpi_readiness": {"status": "ready"},
    }


def _source_facts(**overrides: object) -> dict:
    facts = {
        "last_updated_at": FRESH_TS,
        "total_marketing_spend": 1_000.0,
        "ad_spend": 1_000.0,
        "campaign_spend_by_channel": {
            "google": 600.0,
            "linkedin": 250.0,
            "meta": 150.0,
        },
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
        "source_currencies": {
            "ads": "USD",
            "crm": "USD",
            "finance": "USD",
        },
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


def _projection(
    facts: dict | None = None,
    *,
    setup_rows: list[dict] | None = None,
    contract_rows: list[dict] | None = None,
) -> dict:
    return build_unified_cmo_kpi_projection(
        connector_setup=setup_rows or _setup_rows(),
        connector_contracts=contract_rows or _contract_rows(),
        data_readiness=_data_readiness(),
        source_data=facts or _source_facts(),
        now=NOW,
    )


def _check(projection: dict, key: str) -> dict:
    return next(
        item
        for item in projection["cmo_kpi_reconciliation_checks"]
        if item["reconciliation_key"] == key
    )


def _kpi(projection: dict, key: str) -> dict:
    return next(
        item
        for item in projection["unified_cmo_kpi_results"]
        if item["kpi_key"] == key
    )


def test_matching_ad_spend_totals_pass_reconciliation() -> None:
    projection = _projection()
    spend = _check(projection, "paid_spend_totals_by_channel")

    assert spend["status"] == "passed"
    assert spend["expected_value"] == 1_000.0
    assert spend["observed_value"] == 1_000.0
    assert projection["cmo_kpi_reconciliation_summary"]["readiness"] == "ready"


def test_spend_mismatch_fails_and_blocks_cac_roas_readiness() -> None:
    projection = _projection(
        _source_facts(campaign_spend_by_channel={"google": 400.0, "linkedin": 150.0})
    )
    spend = _check(projection, "paid_spend_totals_by_channel")
    cac = _kpi(projection, "cac")
    roas = _kpi(projection, "roas")

    assert spend["status"] == "failed"
    assert spend["severity"] == "high"
    assert spend["delta_percentage"] == 45.0
    assert cac["status"] == "blocked"
    assert roas["status"] == "blocked"
    assert cac["confidence"] == 0.0
    assert spend["decision_audit_ref"].startswith("cmo_decision_audit:")


def test_ad_conversions_vs_crm_mql_mismatch_downgrades_affected_kpis() -> None:
    projection = _projection(
        _source_facts(ad_platform_conversions=180, crm_campaign_attributed_mqls=100)
    )
    check = _check(projection, "ad_conversions_vs_crm_attribution")
    mql = _kpi(projection, "mql")

    assert check["status"] == "failed"
    assert check["severity"] == "medium"
    assert mql["status"] == "degraded"
    assert mql["value"] == 100.0
    assert mql["confidence"] < 0.8


def test_ga4_conversion_events_without_crm_leads_fail_reconciliation() -> None:
    projection = _projection(_source_facts(ga4_conversion_events=250, crm_leads_created=0))
    check = _check(projection, "ga4_web_conversions_vs_crm_leads")
    conversion = _kpi(projection, "conversion_rates_by_funnel_stage")

    assert check["status"] == "failed"
    assert check["delta_percentage"] == 100.0
    assert conversion["status"] == "degraded"
    assert conversion["next_action_cta"] == "review_web_to_crm_lead_mapping"


def test_email_unsubscribe_mismatch_downgrades_email_performance_kpi() -> None:
    projection = _projection(_source_facts(crm_unsubscribed_contacts=80))
    check = _check(projection, "email_engagement_vs_crm_list")
    email = _kpi(projection, "email_performance")

    assert check["status"] == "failed"
    assert check["observed_value"]["crm_unsubscribed_contacts"] == 80.0
    assert email["status"] == "degraded"
    assert email["reconciliation_status"] == "failed"


def test_content_traffic_mismatch_downgrades_content_performance_kpi() -> None:
    projection = _projection(_source_facts(ga4_content_sessions=700))
    check = _check(projection, "content_traffic_vs_ga4")
    content = _kpi(projection, "content_performance")

    assert check["status"] == "failed"
    assert check["delta_percentage"] == 30.0
    assert content["status"] == "degraded"
    assert content["next_action_cta"] == "review_content_analytics_mapping"


def test_abm_account_domain_mismatch_blocks_or_degrades_abm_readiness() -> None:
    projection = _projection(
        _source_facts(
            abm_target_account_domains=["alpha.example", "beta.example"],
            crm_account_domains=["alpha.example"],
            intent_account_domains=["alpha.example"],
        )
    )
    check = _check(projection, "abm_account_domain_consistency")
    abm = _kpi(projection, "abm_intent_account_readiness")

    assert check["status"] == "failed"
    assert check["severity"] == "high"
    assert "beta.example" in check["missing_requirements"]["domains"]
    assert abm["status"] == "blocked"


def test_currency_mismatch_fails_and_blocks_financial_kpis() -> None:
    projection = _projection(
        _source_facts(source_currencies={"ads": "USD", "crm": "INR", "finance": "USD"})
    )
    check = _check(projection, "currency_consistency")
    cac = _kpi(projection, "cac")

    assert check["status"] == "failed"
    assert check["blocks_kpi_readiness"] is True
    assert cac["status"] == "blocked"
    assert cac["next_action_cta"] == "align_source_currency_mapping"


def test_timezone_mismatch_warns_and_lowers_time_based_kpi_confidence() -> None:
    projection = _projection(
        _source_facts(
            source_timezones={
                "ads": "UTC",
                "crm": "Asia/Kolkata",
                "analytics": "Asia/Kolkata",
            }
        )
    )
    check = _check(projection, "timezone_consistency")
    mql = _kpi(projection, "mql")

    assert check["status"] == "warning"
    assert check["severity"] == "medium"
    assert mql["status"] == "degraded"
    assert mql["confidence"] < 0.8


def test_stale_source_data_downgrades_reconciliation_freshness_and_confidence() -> None:
    projection = _projection(
        setup_rows=_setup_rows(health_overrides={"Ads": "stale"}),
    )
    check = _check(projection, "source_freshness_and_partial_data")
    roas = _kpi(projection, "roas")

    assert check["status"] == "warning"
    assert check["freshness_impact"] > 0
    assert "ads" in check["observed_value"]["stale_sources"]
    assert roas["status"] == "degraded"


def test_partial_data_creates_warning_not_pass_and_degrades_affected_kpi() -> None:
    projection = _projection(
        contract_rows=_contract_rows(failure_overrides={"Analytics": "partial_data"}),
    )
    check = _check(projection, "source_freshness_and_partial_data")
    content = _kpi(projection, "content_performance")

    assert check["status"] == "warning"
    assert check["confidence_impact"] > 0
    assert "analytics" in check["observed_value"]["partial_sources"]
    assert content["status"] == "degraded"


def test_missing_required_source_creates_blocked_status_with_cta() -> None:
    facts = _source_facts()
    facts.pop("campaign_spend_by_channel")
    projection = _projection(facts)
    check = _check(projection, "paid_spend_totals_by_channel")

    assert check["status"] == "blocked"
    assert "campaign_level_spend" in check["missing_requirements"]["fields"]
    assert check["next_action_cta"] == "resolve_spend_reconciliation"


@pytest.mark.asyncio
async def test_kpis_cmo_response_exposes_reconciliation_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_base(tenant_id: str, role: str, company_id: str) -> dict:
        return {
            "demo": False,
            "stale": False,
            "source": "computed",
            "company_id": company_id,
            "agent_count": 0,
            "total_tasks_30d": 0,
        }

    async def fake_configs(tenant_id: str, company_id: str | None = None) -> list:
        return []

    async def fake_approval_timeout(tenant_id: str, company_id: str) -> dict:
        return {"pending": 0, "overdue": 0}

    monkeypatch.setattr(kpis_api, "_build_kpi_response", fake_base)
    monkeypatch.setattr(kpis_api, "_load_marketing_connector_configs", fake_configs)
    monkeypatch.setattr(kpis_api, "_load_cmo_approval_timeout_risk", fake_approval_timeout)

    response = await kpis_api._build_cmo_kpi_response("tenant-1", "company-1")

    assert "cmo_kpi_reconciliation_checks" in response
    assert "cmo_kpi_reconciliation_summary" in response
    assert response["cmo_kpi_reconciliation_summary"]["readiness"] == "blocked"


def test_failed_reconciliation_changes_unified_kpi_result_status_and_confidence() -> None:
    clean = _projection()
    failed = _projection(
        _source_facts(source_currencies={"ads": "USD", "crm": "EUR", "finance": "USD"})
    )

    assert _kpi(clean, "pipeline_contribution")["status"] == "ready"
    assert _kpi(failed, "pipeline_contribution")["status"] == "blocked"
    assert _kpi(failed, "pipeline_contribution")["confidence"] == 0.0
