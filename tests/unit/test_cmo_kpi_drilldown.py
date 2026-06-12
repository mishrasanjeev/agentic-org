from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.v1 import kpis as kpis_api
from core.marketing.kpi_drilldown import build_cmo_kpi_drilldown_projection
from core.marketing.kpi_schema import build_unified_cmo_kpi_projection
from core.marketing.report_quality import build_cmo_report_quality_projection
from core.marketing.work_queue import build_cmo_work_queue_projection

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
            "account_id": f"{category.lower()}-acct",
        }
        for category in categories
    ]


def _contract_rows(
    *,
    categories: tuple[str, ...] = ALL_CONNECTOR_CATEGORIES,
    mock: bool = False,
) -> list[dict]:
    return [
        {
            "connector_key": category.lower(),
            "category": category,
            "configured_status": "configured",
            "read_ready": True,
            "read_status": "ready",
            "write_status": "ready",
            "health_status": "healthy",
            "contract_state": "healthy",
            "blocks_production_kpi_confidence": False,
            "mock_or_test_double": mock,
            "degraded_mode": {"status": "none", "confidence_impact": 0.0},
        }
        for category in categories
    ]


def _data_readiness(
    *,
    mapping_overrides: dict[str, str] | None = None,
    backfill_overrides: dict[str, str] | None = None,
    categories: tuple[str, ...] = ALL_CONNECTOR_CATEGORIES,
) -> dict:
    mapping_overrides = mapping_overrides or {}
    backfill_overrides = backfill_overrides or {}
    return {
        "field_mapping_status": [
            {
                "key": key,
                "name": key.replace("_", " ").title(),
                "status": mapping_overrides.get(key, "valid"),
                "sources": ["hubspot"],
                "decision_audit_ref": f"audit-mapping-{key}",
            }
            for key in ALL_MAPPING_KEYS
        ],
        "backfill_status": [
            {
                "source_connector_key": category.lower(),
                "source_name": f"{category} connector",
                "category": category,
                "status": backfill_overrides.get(category, "completed"),
                "last_run_at": FRESH_TS,
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
        "opportunities": [{"amount": 2_500.0, "id": "opp-1"}, {"amount": 1_500.0, "id": "opp-2"}],
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


def _workflow_activation() -> dict:
    return {
        "workflow_activation_status": [
            {"workflow_key": "weekly_marketing_report", "state": "active"},
            {"workflow_key": "daily_spend_optimization", "state": "active"},
        ],
        "workflow_activation_summary": {"readiness": "ready"},
    }


def _full_projection(
    facts: dict | None = None,
    *,
    setup_rows: list[dict] | None = None,
    contract_rows: list[dict] | None = None,
    data_readiness: dict | None = None,
    source_context: dict | None = None,
) -> dict:
    facts = facts or _source_facts()
    setup = setup_rows or _setup_rows()
    contracts = contract_rows or _contract_rows()
    readiness = data_readiness or _data_readiness()
    unified = build_unified_cmo_kpi_projection(
        connector_setup=setup,
        connector_contracts=contracts,
        data_readiness=readiness,
        source_data=facts,
        now=NOW,
    )
    report_quality = build_cmo_report_quality_projection(
        kpi_results=unified["unified_cmo_kpi_results"],
        reconciliation_checks=unified["cmo_kpi_reconciliation_checks"],
        connector_setup=setup,
        connector_contracts=contracts,
        data_readiness=readiness,
        workflow_activation=_workflow_activation(),
        source_context=source_context or {"demo": False, "source": "computed"},
        now=NOW,
    )
    work_queue = build_cmo_work_queue_projection(
        kpi_results=unified["unified_cmo_kpi_results"],
        reconciliation_checks=unified["cmo_kpi_reconciliation_checks"],
        report_quality_gates=report_quality["report_quality_gates"],
        connector_setup=setup,
        connector_contracts=contracts,
        data_readiness=readiness,
        source_context=source_context or {"demo": False, "source": "computed"},
        now=NOW,
    )
    drilldown = build_cmo_kpi_drilldown_projection(
        kpi_schema=unified["unified_cmo_kpi_schema"],
        kpi_results=unified["unified_cmo_kpi_results"],
        reconciliation_checks=unified["cmo_kpi_reconciliation_checks"],
        connector_setup=setup,
        connector_contracts=contracts,
        data_readiness=readiness,
        work_queue=work_queue["cmo_work_queue"],
        report_quality_gates=report_quality["report_quality_gates"],
        source_data=facts,
        source_context=source_context or {"demo": False, "source": "computed"},
        now=NOW,
    )
    return {**unified, **report_quality, **work_queue, **drilldown}


def _drilldown(projection: dict, key: str) -> dict:
    return next(item for item in projection["cmo_kpi_drilldowns"] if item["kpi_key"] == key)


def _input(drilldown: dict, name: str) -> dict:
    return next(item for item in drilldown["formula_inputs"] if item["name"] == name)


def test_cac_drilldown_includes_formula_inputs_sources_confidence_and_cta() -> None:
    projection = _full_projection()
    cac = _drilldown(projection, "cac")

    assert cac["formula"] == "total_marketing_spend / new_customers"
    assert _input(cac, "total_marketing_spend")["value"] == 1_000.0
    assert _input(cac, "new_customers")["value"] == 10
    assert any(ref.get("connector_key") == "hubspot" for ref in cac["source_refs"])
    assert any(ref.get("category") == "Ads" for ref in cac["connector_refs"])
    assert cac["confidence"] > 0.9
    assert cac["next_action_cta"]["action_key"] == "none"


def test_mql_and_sql_drilldowns_include_lifecycle_mapping_and_crm_refs() -> None:
    projection = _full_projection()
    mql = _drilldown(projection, "mql")
    sql = _drilldown(projection, "sql")

    assert _input(mql, "mql_count")["value"] == 100
    assert _input(sql, "sql_count")["value"] == 40
    assert any(row["key"] == "lifecycle_stages" for row in mql["field_mappings_used"])
    assert any(ref.get("category") == "CRM" for ref in mql["connector_refs"])


def test_mql_to_sql_drilldown_handles_zero_denominator_and_explains_degraded_state() -> None:
    projection = _full_projection(_source_facts(lifecycle_stage_counts={"mql": 0, "sql": 0}))
    conversion = _drilldown(projection, "mql_to_sql_conversion_rate")

    assert conversion["status"] == "degraded"
    assert conversion["value"] == 0.0
    assert any("Zero denominator" in reason for reason in conversion["confidence_impact_reasons"])
    assert _input(conversion, "mql_count")["value"] == 0


def test_roas_drilldown_includes_revenue_spend_inputs_and_reconciliation_refs() -> None:
    projection = _full_projection(_source_facts(campaign_spend_by_channel={"google": 1_300.0}))
    roas = _drilldown(projection, "roas")

    assert _input(roas, "attributed_revenue")["value"] == 5_000.0
    assert _input(roas, "ad_spend")["value"] == 1_000.0
    assert any(check["reconciliation_key"] == "paid_spend_totals_by_channel" for check in roas["reconciliation_checks"])
    assert any(ref.startswith("cmo_decision_audit") for ref in roas["audit_refs"])


def test_pipeline_contribution_drilldown_includes_opportunity_mapping_and_refs() -> None:
    projection = _full_projection()
    pipeline = _drilldown(projection, "pipeline_contribution")

    assert pipeline["value"] == 4_000.0
    assert _input(pipeline, "pipeline_contribution")["value"] == 4_000.0
    assert any(row["key"] == "opportunity_revenue" for row in pipeline["field_mappings_used"])
    assert any(row["category"] == "CRM" for row in pipeline["backfill_state"])


def test_stale_source_data_appears_in_freshness_and_confidence_reasons() -> None:
    projection = _full_projection(_source_facts(last_updated_at=STALE_TS))
    roas = _drilldown(projection, "roas")

    assert roas["status"] == "degraded"
    assert roas["freshness_status"] == "stale"
    assert any("stale" in reason.lower() for reason in roas["confidence_impact_reasons"])
    assert roas["next_action_cta"]["action_key"] == "refresh_source_data"


def test_missing_mapping_and_backfill_appear_as_blockers_with_cta() -> None:
    readiness = _data_readiness(
        mapping_overrides={"opportunity_revenue": "unmapped"},
        backfill_overrides={"Ads": "failed"},
    )
    projection = _full_projection(data_readiness=readiness)
    cac = _drilldown(projection, "cac")

    assert cac["status"] == "blocked"
    assert "opportunity_revenue" in cac["missing_requirements"]["field_mappings"]
    assert "Ads" in cac["missing_requirements"]["backfills"]
    assert cac["next_action_cta"]["action_key"] == "fix_required_mapping"
    assert any(row["status"] == "unmapped" for row in cac["field_mappings_used"])


def test_related_work_queue_and_report_gate_refs_appear_when_applicable() -> None:
    projection = _full_projection(_source_facts(campaign_spend_by_channel={"google": 1_300.0}))
    cac = _drilldown(projection, "cac")

    assert any(item_id.startswith("cmo_wq_") for item_id in cac["related_work_queue_item_ids"])
    assert "weekly_marketing_report" in cac["related_report_gate_ids"]


@pytest.mark.parametrize(
    "kpi_key",
    [
        "cac",
        "mql",
        "sql",
        "mql_to_sql_conversion_rate",
        "roas",
        "pipeline_contribution",
        "experiment_velocity",
        "content_performance",
        "email_performance",
        "brand_sentiment",
        "abm_intent_account_readiness",
    ],
)
def test_required_core_drilldowns_are_present(kpi_key: str) -> None:
    projection = _full_projection()

    assert _drilldown(projection, kpi_key)["drilldown_id"] == f"cmo_kpi_drilldown:{kpi_key}"


@pytest.mark.asyncio
async def test_kpis_cmo_response_exposes_kpi_drilldowns(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert "cmo_kpi_drilldowns" in response
    assert "cmo_kpi_drilldown_summary" in response
    assert response["cmo_kpi_drilldown_summary"]["total"] >= 11


def test_drilldown_does_not_claim_demo_or_mock_lineage_as_production_proof() -> None:
    projection = _full_projection(
        _source_facts(source="sample"),
        contract_rows=_contract_rows(mock=True),
        source_context={"demo": True, "source": "demo"},
    )
    cac = _drilldown(projection, "cac")

    assert cac["production_lineage_ready"] is False
    assert cac["production_lineage_status"] == "blocked"
    assert cac["source_refs"] == []
    assert "real_production_lineage" in cac["missing_requirements"]["source_data"]
    assert cac["next_action_cta"]["action_key"] == "connect_real_marketing_sources"
