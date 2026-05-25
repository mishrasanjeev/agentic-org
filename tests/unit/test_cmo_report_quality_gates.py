from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.v1 import kpis as kpis_api
from core.marketing.kpi_schema import build_unified_cmo_kpi_projection
from core.marketing.report_quality import (
    build_cmo_report_quality_projection,
    cmo_report_gate_for_type,
    cmo_report_trusted_delivery_allowed,
)

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
FRESH_TS = (NOW - timedelta(hours=1)).isoformat()
STALE_TS = (NOW - timedelta(days=3)).isoformat()

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
) -> list[dict]:
    return [
        {
            "connector_key": category.lower(),
            "category": category,
            "configured_status": "configured",
            "read_ready": True,
            "read_status": "ready",
            "failure_class": None,
            "blocks_production_kpi_confidence": False,
            "degraded_mode": {"status": "none", "confidence_impact": 0.0},
        }
        for category in categories
    ]


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


def _workflow_activation(*, state: str = "active") -> dict:
    return {
        "workflow_activation_status": [
            {"workflow_key": "weekly_marketing_report", "state": state},
            {"workflow_key": "daily_spend_optimization", "state": state},
        ],
        "workflow_activation_summary": {"readiness": "ready"},
    }


def _unified_projection(
    facts: dict | None = None,
    *,
    setup_rows: list[dict] | None = None,
    contract_rows: list[dict] | None = None,
    data_readiness: dict | None = None,
) -> dict:
    return build_unified_cmo_kpi_projection(
        connector_setup=setup_rows or _setup_rows(),
        connector_contracts=contract_rows or _contract_rows(),
        data_readiness=data_readiness or _data_readiness(),
        source_data=facts or _source_facts(),
        now=NOW,
    )


def _report_projection(
    unified: dict | None = None,
    *,
    setup_rows: list[dict] | None = None,
    contract_rows: list[dict] | None = None,
    data_readiness: dict | None = None,
    workflow_state: str = "active",
    source_context: dict | None = None,
    report_context: dict | None = None,
    production_tenant: bool = False,
) -> dict:
    unified = unified or _unified_projection(
        setup_rows=setup_rows,
        contract_rows=contract_rows,
        data_readiness=data_readiness,
    )
    return build_cmo_report_quality_projection(
        kpi_results=unified["unified_cmo_kpi_results"],
        reconciliation_checks=unified["cmo_kpi_reconciliation_checks"],
        connector_setup=setup_rows or _setup_rows(),
        data_readiness=data_readiness or _data_readiness(),
        connector_contracts=contract_rows or _contract_rows(),
        workflow_activation=_workflow_activation(state=workflow_state),
        source_context=source_context or {"demo": False, "source": "computed"},
        report_context=report_context,
        production_tenant=production_tenant,
        now=NOW,
    )


def _gate(projection: dict, key: str) -> dict:
    return next(item for item in projection["report_quality_gates"] if item["report_key"] == key)


def _kpi_result(key: str, **overrides: object) -> dict:
    row = {
        "kpi_key": key,
        "status": "ready",
        "confidence": 0.92,
        "freshness_status": "fresh",
        "source_refs": [{"connector_key": "test", "object": key}],
        "missing_requirements": {},
        "next_action_cta": "none",
    }
    row.update(overrides)
    return row


def test_weekly_report_passes_when_required_kpis_ready_and_reconciliation_passes() -> None:
    projection = _report_projection()
    gate = _gate(projection, "weekly_marketing_report")

    assert gate["status"] == "pass"
    assert gate["safe_report_mode"] == "deliverable"
    assert gate["trusted_delivery_allowed"] is True
    assert projection["report_quality_summary"]["readiness"] == "ready"


def test_weekly_report_blocks_when_cac_or_pipeline_contribution_is_blocked() -> None:
    unified = _unified_projection(_source_facts(new_customers=None, opportunities=[]))
    projection = _report_projection(unified)
    gate = _gate(projection, "weekly_marketing_report")

    assert gate["status"] == "blocked"
    assert gate["safe_report_mode"] == "draft_only"
    assert {"cac", "pipeline_contribution"} <= set(gate["blocked_kpi_keys"])


def test_daily_ad_performance_blocks_when_ad_source_is_missing() -> None:
    setup_rows = _setup_rows(categories=("CRM", "Analytics", "CMS", "Email", "Brand", "ABM", "Finance"))
    contract_rows = _contract_rows(categories=("CRM", "Analytics", "CMS", "Email", "Brand", "ABM", "Finance"))
    data_readiness = _data_readiness(categories=("CRM", "Analytics", "CMS", "Email", "Brand", "ABM", "Finance"))
    unified = _unified_projection(
        setup_rows=setup_rows,
        contract_rows=contract_rows,
        data_readiness=data_readiness,
    )
    projection = _report_projection(
        unified,
        setup_rows=setup_rows,
        contract_rows=contract_rows,
        data_readiness=data_readiness,
    )
    gate = _gate(projection, "daily_ad_performance")

    assert gate["status"] == "blocked"
    assert "Ads" in gate["missing_requirements"]["connectors"]
    assert gate["next_action_cta"] == "fix_report_connectors"


def test_monthly_roi_blocks_when_reconciliation_failed_or_confidence_below_floor() -> None:
    unified = _unified_projection(
        _source_facts(campaign_spend_by_channel={"google": 400.0, "linkedin": 150.0})
    )
    projection = _report_projection(unified)
    gate = _gate(projection, "monthly_marketing_roi")

    assert gate["status"] == "blocked"
    assert "paid_spend_totals_by_channel" in gate["failed_reconciliation_keys"]
    assert {"cac", "roas", "ltv_cac"} & set(gate["blocked_kpi_keys"])
    assert gate["safe_report_mode"] == "draft_only"


def test_campaign_ad_hoc_report_warns_for_stale_optional_data() -> None:
    kpis = [
        _kpi_result("roas"),
        _kpi_result("mql"),
        _kpi_result("sql"),
        _kpi_result("conversion_rates_by_funnel_stage"),
        _kpi_result(
            "content_performance",
            status="degraded",
            confidence=0.62,
            freshness_status="stale",
        ),
    ]
    projection = build_cmo_report_quality_projection(
        kpi_results=kpis,
        reconciliation_checks=[],
        connector_setup=_setup_rows(),
        data_readiness=_data_readiness(),
        connector_contracts=_contract_rows(),
        workflow_activation=_workflow_activation(),
        source_context={"demo": False, "source": "computed"},
        now=NOW,
    )
    gate = _gate(projection, "campaign_performance_ad_hoc")

    assert gate["status"] == "warning"
    assert gate["safe_report_mode"] == "internal_only"
    assert "content_performance" in gate["degraded_kpi_keys"]


def test_draft_only_mode_allowed_for_incomplete_internal_report() -> None:
    unified = _unified_projection(_source_facts(total_marketing_spend=None, ad_spend=None))
    projection = _report_projection(unified)
    gate = _gate(projection, "weekly_marketing_report")

    assert gate["status"] == "blocked"
    assert gate["safe_report_mode"] == "draft_only"
    assert gate["trusted_delivery_allowed"] is False


def test_deliverable_mode_denied_for_demo_or_hardcoded_production_data() -> None:
    projection = _report_projection(
        source_context={"demo": True, "source": "hardcoded"},
        production_tenant=True,
    )
    gate = _gate(projection, "weekly_marketing_report")

    assert gate["status"] == "blocked"
    assert gate["safe_report_mode"] == "draft_only"
    assert "real_tenant_kpi_data" in gate["missing_requirements"]["source_data"]


def test_failed_reconciliation_blocks_affected_report() -> None:
    unified = _unified_projection(_source_facts(source_currencies={"ads": "USD", "crm": "EUR"}))
    projection = _report_projection(unified)
    gate = _gate(projection, "weekly_marketing_report")

    assert gate["status"] == "blocked"
    assert "currency_consistency" in gate["failed_reconciliation_keys"]
    assert gate["next_action_cta"] in {"resolve_report_reconciliation", "restore_required_kpis"}


def test_stale_critical_source_blocks_trusted_weekly_report() -> None:
    unified = _unified_projection(_source_facts(last_updated_at=STALE_TS))
    projection = _report_projection(unified)
    gate = _gate(projection, "weekly_marketing_report")

    assert gate["status"] == "blocked"
    assert gate["stale_missing_source_refs"]
    assert gate["next_action_cta"] == "refresh_report_sources"


def test_missing_policy_audit_or_approval_blocks_sensitive_report_delivery() -> None:
    projection = _report_projection(
        report_context={
            "weekly_marketing_report": {
                "external_delivery_requested": True,
                "contains_sensitive_claims": True,
            }
        },
    )
    gate = _gate(projection, "weekly_marketing_report")

    assert gate["status"] == "blocked"
    assert "delivery_approval_ref" in gate["missing_requirements"]["approval"]
    assert gate["safe_report_mode"] == "draft_only"


@pytest.mark.asyncio
async def test_kpis_cmo_response_exposes_report_quality_gate_summary(monkeypatch: pytest.MonkeyPatch) -> None:
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
        return {"pending": 0, "overdue": 0}

    monkeypatch.setattr(kpis_api, "_build_kpi_response", fake_base)
    monkeypatch.setattr(kpis_api, "_load_marketing_connector_configs", fake_configs)
    monkeypatch.setattr(kpis_api, "_load_cmo_approval_timeout_risk", fake_approval_timeout)

    response = await kpis_api._build_cmo_kpi_response("tenant-1", "company-1")

    assert "report_quality_gates" in response
    assert "report_quality_summary" in response
    assert response["report_quality_summary"]["readiness"] == "blocked"


def test_cmo_report_delivery_helper_denies_non_deliverable_gate() -> None:
    payload = {
        "demo": True,
        "source": "report_generator_fallback",
        "unified_cmo_kpi_results": [],
        "cmo_kpi_reconciliation_checks": [],
    }

    gate = cmo_report_gate_for_type("cmo_weekly", payload, production_tenant=True)

    assert gate is not None
    assert gate["safe_report_mode"] == "draft_only"
    assert cmo_report_trusted_delivery_allowed(gate) is False


def test_cmo_report_generator_labels_fallback_report_as_draft_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.reports.generator import ReportGenerator

    monkeypatch.setattr(
        ReportGenerator,
        "_fetch_cmo_kpis",
        staticmethod(lambda company_id: {"demo": True, "source": "report_generator_fallback"}),
    )

    output = ReportGenerator().generate("cmo_weekly", params={})
    gate = output.content_data["report_quality_gate"]

    assert gate["safe_report_mode"] == "draft_only"
    assert gate["trusted_delivery_allowed"] is False
    assert "Report quality gate" in output.content_html
