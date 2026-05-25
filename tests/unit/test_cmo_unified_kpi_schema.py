from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.marketing.kpi_schema import (
    CMO_KPI_SCHEMA_VERSION,
    build_unified_cmo_kpi_projection,
    default_cmo_kpi_schema,
    evaluate_cmo_kpi,
    evaluate_cmo_kpis,
)

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


def _setup_rows(*, categories: tuple[str, ...] = ALL_CONNECTOR_CATEGORIES) -> list[dict]:
    return [
        {
            "key": category.lower(),
            "name": f"{category} connector",
            "category": category,
            "configured_status": "configured",
            "health_status": "healthy",
            "last_sync_at": FRESH_TS,
        }
        for category in categories
    ]


def _contract_rows(*, categories: tuple[str, ...] = ALL_CONNECTOR_CATEGORIES) -> list[dict]:
    return [
        {
            "connector_key": category.lower(),
            "category": category,
            "configured_status": "configured",
            "read_ready": True,
            "read_status": "ready",
            "blocks_production_kpi_confidence": False,
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
        "new_customers": 10,
        "lifecycle_stage_counts": {
            "visitor": 1_000,
            "lead": 250,
            "mql": 100,
            "sql": 40,
            "opportunity": 20,
            "customer": 10,
        },
        "attributed_revenue": 5_000.0,
        "opportunities": [{"amount": 2_500.0}, {"amount": 1_500.0}],
        "customer_ltv": 2_000.0,
        "completed_experiments": 6,
        "period_days": 30,
        "content_sessions": 1_000,
        "content_conversions": 50,
        "emails_sent": 2_000,
        "emails_opened": 900,
        "emails_clicked": 300,
        "emails_unsubscribed": 20,
        "brand_positive_mentions": 70,
        "brand_negative_mentions": 10,
        "brand_neutral_mentions": 20,
        "intent_ready_accounts": 25,
        "target_accounts": 100,
        "source_refs": [{"connector_key": "hubspot", "object": "deals"}],
        "audit_refs": ["audit-kpi-source"],
    }
    facts.update(overrides)
    return facts


def _evaluate(key: str, source_data: dict | None = None, **readiness_overrides: object) -> dict:
    return evaluate_cmo_kpi(
        key,
        source_data or _source_facts(),
        connector_setup=_setup_rows(),
        connector_contracts=_contract_rows(),
        data_readiness=_data_readiness(**readiness_overrides),
        now=NOW,
    )


def test_required_kpi_definitions_load_with_stable_keys_and_formulas() -> None:
    schema = default_cmo_kpi_schema()
    by_key = {item["key"]: item for item in schema["kpis"]}

    assert schema["schema_version"] == CMO_KPI_SCHEMA_VERSION
    assert set(by_key) == {
        "cac",
        "mql",
        "sql",
        "mql_to_sql_conversion_rate",
        "roas",
        "pipeline_contribution",
        "conversion_rates_by_funnel_stage",
        "ltv_cac",
        "experiment_velocity",
        "content_performance",
        "email_performance",
        "brand_sentiment",
        "abm_intent_account_readiness",
    }
    assert by_key["cac"]["formula"] == "total_marketing_spend / new_customers"
    assert by_key["roas"]["formula"] == "attributed_revenue / ad_spend"
    assert by_key["pipeline_contribution"]["required_field_mappings"] == [
        "opportunity_revenue",
        "campaign_ids",
        "currency",
    ]


def test_cac_formula_computes_from_spend_and_new_customers() -> None:
    result = _evaluate("cac")

    assert result["status"] == "ready"
    assert result["value"] == 100.0
    assert result["unit"] == "currency"
    assert result["confidence"] > 0.9


def test_cac_blocks_when_required_source_mapping_is_missing() -> None:
    result = _evaluate(
        "cac",
        mapping_overrides={"opportunity_revenue": "unmapped"},
    )

    assert result["status"] == "blocked"
    assert result["value"] is None
    assert "opportunity_revenue" in result["missing_requirements"]["field_mappings"]
    assert result["next_action_cta"] == "fix_required_mapping"


def test_mql_and_sql_counts_compute_from_mapped_lifecycle_stages() -> None:
    mql = _evaluate("mql")
    sql = _evaluate("sql")

    assert mql["status"] == "ready"
    assert mql["value"] == 100.0
    assert sql["status"] == "ready"
    assert sql["value"] == 40.0


def test_mql_to_sql_conversion_handles_zero_denominator_safely() -> None:
    result = _evaluate(
        "mql_to_sql_conversion_rate",
        _source_facts(lifecycle_stage_counts={"mql": 0, "sql": 0}),
    )

    assert result["status"] == "degraded"
    assert result["value"] == 0.0
    assert result["zero_denominator"] is True
    assert result["blocked_reasons"] == []


def test_roas_computes_and_degrades_when_attribution_is_partial() -> None:
    ready = _evaluate("roas")
    degraded = _evaluate(
        "roas",
        _source_facts(attribution_status="partial", attribution_coverage=0.6),
    )

    assert ready["status"] == "ready"
    assert ready["value"] == 5.0
    assert degraded["status"] == "degraded"
    assert degraded["value"] == 5.0
    assert any("Attribution coverage is partial" in reason for reason in degraded["degraded_reasons"])


def test_roas_blocks_when_attribution_source_data_is_missing() -> None:
    facts = _source_facts()
    facts.pop("attributed_revenue")

    result = _evaluate("roas", facts)

    assert result["status"] == "blocked"
    assert result["value"] is None
    assert "attributed_revenue" in result["missing_requirements"]["fields"]


def test_pipeline_contribution_and_experiment_velocity_compute_from_structured_inputs() -> None:
    pipeline = _evaluate("pipeline_contribution")
    velocity = _evaluate("experiment_velocity")

    assert pipeline["status"] == "ready"
    assert pipeline["value"] == 4_000.0
    assert velocity["status"] == "ready"
    assert velocity["value"] == 6.0


def test_conversion_rates_by_funnel_stage_compute_with_safe_zero_denominators() -> None:
    result = _evaluate(
        "conversion_rates_by_funnel_stage",
        _source_facts(lifecycle_stage_counts={"visitor": 0, "lead": 0, "mql": 10, "sql": 5}),
    )

    assert result["status"] == "degraded"
    assert result["value"]["visitor_to_lead"] == 0.0
    assert result["value"]["mql_to_sql"] == 50.0
    assert result["zero_denominator"] is True


def test_stale_source_data_downgrades_confidence_and_freshness() -> None:
    result = _evaluate(
        "roas",
        _source_facts(last_updated_at=(NOW - timedelta(days=2)).isoformat()),
    )

    assert result["status"] == "degraded"
    assert result["freshness_status"] == "stale"
    assert result["confidence"] < 0.8
    assert result["next_action_cta"] == "refresh_source_data"


def test_partial_source_readiness_degrades_instead_of_reporting_fake_ready() -> None:
    result = _evaluate(
        "roas",
        mapping_overrides={"utm_fields": "partially_mapped"},
    )

    assert result["status"] == "degraded"
    assert result["value"] == 5.0
    assert "utm_fields" not in result["missing_requirements"]["field_mappings"]
    assert any("utm_fields" in reason for reason in result["degraded_reasons"])


def test_missing_connector_mapping_and_backfill_block_affected_kpi() -> None:
    missing_connector = evaluate_cmo_kpi(
        "roas",
        _source_facts(),
        connector_setup=_setup_rows(categories=("CRM", "Analytics")),
        connector_contracts=_contract_rows(categories=("CRM", "Analytics")),
        data_readiness=_data_readiness(categories=("CRM", "Analytics")),
        now=NOW,
    )
    failed_backfill = _evaluate(
        "roas",
        backfill_overrides={"Analytics": "failed"},
    )

    assert missing_connector["status"] == "blocked"
    assert "Ads" in missing_connector["missing_requirements"]["connectors"]
    assert failed_backfill["status"] == "blocked"
    assert "Analytics" in failed_backfill["missing_requirements"]["backfills"]


def test_kpi_result_includes_formula_lineage_source_refs_and_action() -> None:
    result = _evaluate("cac")

    assert result["formula_refs"]["schema_version"] == CMO_KPI_SCHEMA_VERSION
    assert result["formula_refs"]["formula_inputs"] == ["total_marketing_spend", "new_customers"]
    assert result["source_lineage_refs"] == ["ads.spend", "crm.new_customers", "finance.currency"]
    assert any(ref.get("connector_key") == "hubspot" for ref in result["source_refs"])
    assert "audit-kpi-source" in result["audit_refs"]
    assert result["next_action_cta"] == "none"


def test_all_core_kpis_can_evaluate_ready_from_mapped_fresh_real_inputs() -> None:
    results = evaluate_cmo_kpis(
        _source_facts(),
        connector_setup=_setup_rows(),
        connector_contracts=_contract_rows(),
        data_readiness=_data_readiness(),
        now=NOW,
    )

    assert {result["kpi_key"] for result in results} == {
        item["key"] for item in default_cmo_kpi_schema()["kpis"]
    }
    assert all(result["status"] == "ready" for result in results)


def test_projection_blocks_production_kpis_instead_of_using_demo_or_hardcoded_values() -> None:
    projection = build_unified_cmo_kpi_projection(
        connector_setup=[],
        connector_contracts=[],
        data_readiness={"field_mapping_status": [], "backfill_status": []},
        source_data=_source_facts(),
        now=NOW,
    )

    assert projection["unified_cmo_kpi_summary"]["readiness"] == "blocked"
    assert projection["unified_cmo_kpi_summary"]["blocked"] > 0
    assert all(result["value"] is None for result in projection["unified_cmo_kpi_results"])
    assert all(result["status"] == "blocked" for result in projection["unified_cmo_kpi_results"])
