"""Unified CMO KPI schema and deterministic formula helpers.

CMO-7.1 defines canonical marketing KPI definitions and a small evaluation
engine. It consumes the existing connector setup, connector contract, field
mapping, backfill, retry/degraded, and decision-audit projections; it does not
invent production values when real mapped source facts are missing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.marketing.kpi_reconciliation import build_cmo_kpi_reconciliation_projection

CMO_KPI_SCHEMA_VERSION = "2026-05-23.cmo-7.1"

KPI_STATUSES = ("ready", "degraded", "blocked", "unavailable")

COUNT_UNIT = "count"
CURRENCY_UNIT = "currency"
PERCENT_UNIT = "percentage"
RATIO_UNIT = "ratio"
INDEX_UNIT = "index"


@dataclass(frozen=True)
class CmoKpiDefinition:
    key: str
    display_name: str
    description: str
    formula: str
    required_source_domains: tuple[str, ...]
    required_connector_categories: tuple[str, ...]
    required_field_mappings: tuple[str, ...]
    required_backfill_categories: tuple[str, ...]
    refresh_ttl_seconds: int
    unit: str
    owner_role: str
    confidence_rules: tuple[str, ...]
    freshness_rules: tuple[str, ...]
    missing_data_behavior: str
    source_lineage_refs: tuple[str, ...]
    audit_evidence_classes: tuple[str, ...] = (
        "input_snapshot_hash",
        "source_refs",
        "field_mapping_status",
        "backfill_status",
        "connector_contract_status",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "description": self.description,
            "formula": self.formula,
            "required_source_domains": list(self.required_source_domains),
            "required_connector_categories": list(self.required_connector_categories),
            "required_field_mappings": list(self.required_field_mappings),
            "required_backfill_categories": list(self.required_backfill_categories),
            "refresh_cadence_ttl_seconds": self.refresh_ttl_seconds,
            "unit": self.unit,
            "owner_role": self.owner_role,
            "confidence_rules": list(self.confidence_rules),
            "freshness_rules": list(self.freshness_rules),
            "missing_data_behavior": self.missing_data_behavior,
            "source_lineage_refs": list(self.source_lineage_refs),
            "audit_evidence_classes": list(self.audit_evidence_classes),
        }


CMO_KPI_DEFINITIONS: tuple[CmoKpiDefinition, ...] = (
    CmoKpiDefinition(
        key="cac",
        display_name="CAC",
        description="Customer acquisition cost for the evaluated period.",
        formula="total_marketing_spend / new_customers",
        required_source_domains=("Ads", "CRM", "Finance"),
        required_connector_categories=("Ads", "CRM"),
        required_field_mappings=("opportunity_revenue", "campaign_ids", "utm_fields", "currency"),
        required_backfill_categories=("Ads", "CRM"),
        refresh_ttl_seconds=24 * 3600,
        unit=CURRENCY_UNIT,
        owner_role="growth_lead",
        confidence_rules=(
            "Requires mapped spend and new-customer facts.",
            "Connector degraded-mode confidence impact is applied.",
        ),
        freshness_rules=("Spend and CRM facts should be refreshed daily.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("ads.spend", "crm.new_customers", "finance.currency"),
    ),
    CmoKpiDefinition(
        key="mql",
        display_name="MQL",
        description="Marketing qualified leads from mapped lifecycle stages.",
        formula="count(lifecycle_stage == mql)",
        required_source_domains=("CRM",),
        required_connector_categories=("CRM",),
        required_field_mappings=("lifecycle_stages",),
        required_backfill_categories=("CRM",),
        refresh_ttl_seconds=6 * 3600,
        unit=COUNT_UNIT,
        owner_role="revops_lead",
        confidence_rules=("Requires mapped CRM lifecycle stages.",),
        freshness_rules=("CRM lifecycle data should refresh within 6 hours.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("crm.lifecycle_stages",),
    ),
    CmoKpiDefinition(
        key="sql",
        display_name="SQL",
        description="Sales qualified leads from mapped lifecycle stages.",
        formula="count(lifecycle_stage == sql)",
        required_source_domains=("CRM",),
        required_connector_categories=("CRM",),
        required_field_mappings=("lifecycle_stages",),
        required_backfill_categories=("CRM",),
        refresh_ttl_seconds=6 * 3600,
        unit=COUNT_UNIT,
        owner_role="revops_lead",
        confidence_rules=("Requires mapped CRM lifecycle stages.",),
        freshness_rules=("CRM lifecycle data should refresh within 6 hours.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("crm.lifecycle_stages",),
    ),
    CmoKpiDefinition(
        key="mql_to_sql_conversion_rate",
        display_name="MQL-to-SQL conversion rate",
        description="Share of MQLs that became SQLs in the evaluated period.",
        formula="sql_count / mql_count * 100",
        required_source_domains=("CRM",),
        required_connector_categories=("CRM",),
        required_field_mappings=("lifecycle_stages",),
        required_backfill_categories=("CRM",),
        refresh_ttl_seconds=6 * 3600,
        unit=PERCENT_UNIT,
        owner_role="revops_lead",
        confidence_rules=("Zero MQL denominator returns 0 safely and marks denominator context.",),
        freshness_rules=("CRM lifecycle data should refresh within 6 hours.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("crm.lifecycle_stages",),
    ),
    CmoKpiDefinition(
        key="roas",
        display_name="ROAS",
        description="Return on ad spend from attributed revenue and ad spend.",
        formula="attributed_revenue / ad_spend",
        required_source_domains=("Ads", "CRM", "Analytics"),
        required_connector_categories=("Ads", "CRM", "Analytics"),
        required_field_mappings=("opportunity_revenue", "campaign_ids", "utm_fields", "currency"),
        required_backfill_categories=("Ads", "CRM", "Analytics"),
        refresh_ttl_seconds=6 * 3600,
        unit=RATIO_UNIT,
        owner_role="growth_lead",
        confidence_rules=("Attribution gaps degrade confidence.",),
        freshness_rules=("Ads, CRM, and analytics facts should refresh within 6 hours.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("ads.spend", "crm.attributed_revenue", "analytics.attribution"),
    ),
    CmoKpiDefinition(
        key="pipeline_contribution",
        display_name="Pipeline contribution",
        description="Marketing-sourced open pipeline value.",
        formula="sum(marketing_sourced_opportunity.amount)",
        required_source_domains=("CRM", "Finance"),
        required_connector_categories=("CRM",),
        required_field_mappings=("opportunity_revenue", "campaign_ids", "currency"),
        required_backfill_categories=("CRM",),
        refresh_ttl_seconds=6 * 3600,
        unit=CURRENCY_UNIT,
        owner_role="revops_lead",
        confidence_rules=("Requires mapped opportunity amounts and marketing source attribution.",),
        freshness_rules=("CRM opportunity data should refresh within 6 hours.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("crm.opportunities", "crm.campaign_ids"),
    ),
    CmoKpiDefinition(
        key="conversion_rates_by_funnel_stage",
        display_name="Conversion rates by funnel stage",
        description="Stage-to-stage conversion rates across the marketing funnel.",
        formula="next_stage_count / current_stage_count * 100 by ordered funnel stage",
        required_source_domains=("CRM", "Analytics"),
        required_connector_categories=("CRM", "Analytics"),
        required_field_mappings=("lifecycle_stages", "utm_fields"),
        required_backfill_categories=("CRM", "Analytics"),
        refresh_ttl_seconds=6 * 3600,
        unit=PERCENT_UNIT,
        owner_role="revops_lead",
        confidence_rules=("Zero denominators return 0 safely for that stage transition.",),
        freshness_rules=("Funnel data should refresh within 6 hours.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("crm.lifecycle_stages", "analytics.funnel_events"),
    ),
    CmoKpiDefinition(
        key="ltv_cac",
        display_name="LTV/CAC",
        description="Customer lifetime value divided by customer acquisition cost, when LTV is mapped.",
        formula="customer_ltv / cac",
        required_source_domains=("CRM", "Finance", "Ads"),
        required_connector_categories=("CRM", "Ads", "Finance"),
        required_field_mappings=("opportunity_revenue", "currency"),
        required_backfill_categories=("CRM", "Ads"),
        refresh_ttl_seconds=24 * 3600,
        unit=RATIO_UNIT,
        owner_role="growth_lead",
        confidence_rules=("Unavailable until mapped LTV/customer revenue facts exist.",),
        freshness_rules=("Finance and CRM facts should refresh daily.",),
        missing_data_behavior="unavailable_if_ltv_missing",
        source_lineage_refs=("finance.customer_ltv", "ads.spend", "crm.new_customers"),
    ),
    CmoKpiDefinition(
        key="experiment_velocity",
        display_name="Experiment velocity",
        description="Completed experiments normalized to a 30-day period.",
        formula="completed_experiments / period_days * 30",
        required_source_domains=("Ads", "Analytics", "CMS", "Email"),
        required_connector_categories=("Ads", "Analytics"),
        required_field_mappings=("campaign_ids",),
        required_backfill_categories=("Ads", "Analytics"),
        refresh_ttl_seconds=24 * 3600,
        unit=COUNT_UNIT,
        owner_role="growth_lead",
        confidence_rules=("Partial experiment tracking degrades confidence.",),
        freshness_rules=("Experiment registry should refresh daily.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("experiments.completed", "experiments.active"),
    ),
    CmoKpiDefinition(
        key="content_performance",
        display_name="Content performance",
        description="Content conversion and engagement performance.",
        formula="content_conversions / content_sessions * 100 with engagement context",
        required_source_domains=("CMS", "Analytics"),
        required_connector_categories=("CMS", "Analytics"),
        required_field_mappings=("campaign_ids", "utm_fields", "timezone"),
        required_backfill_categories=("CMS", "Analytics"),
        refresh_ttl_seconds=12 * 3600,
        unit=PERCENT_UNIT,
        owner_role="content_lead",
        confidence_rules=("Missing content attribution degrades confidence.",),
        freshness_rules=("Content analytics should refresh within 12 hours.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("cms.content_ids", "analytics.content_events"),
    ),
    CmoKpiDefinition(
        key="email_performance",
        display_name="Email performance",
        description="Email open, click, unsubscribe, and deliverability performance.",
        formula="opens/sent, clicks/sent, unsubscribes/sent",
        required_source_domains=("Email", "CRM"),
        required_connector_categories=("Email",),
        required_field_mappings=("campaign_ids", "consent_unsubscribe", "timezone"),
        required_backfill_categories=("Email",),
        refresh_ttl_seconds=6 * 3600,
        unit=PERCENT_UNIT,
        owner_role="demand_gen_lead",
        confidence_rules=("Consent/unsubscribe mapping is required for trusted email KPIs.",),
        freshness_rules=("Email platform metrics should refresh within 6 hours.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("email.campaign_metrics", "email.unsubscribe_status"),
    ),
    CmoKpiDefinition(
        key="brand_sentiment",
        display_name="Brand sentiment",
        description="Normalized positive/negative/neutral brand sentiment score.",
        formula="(positive_mentions - negative_mentions) / total_mentions",
        required_source_domains=("Brand", "Social"),
        required_connector_categories=("Brand",),
        required_field_mappings=("timezone",),
        required_backfill_categories=("Brand",),
        refresh_ttl_seconds=3 * 3600,
        unit=INDEX_UNIT,
        owner_role="brand_lead",
        confidence_rules=("Low mention volume or degraded brand connector lowers confidence.",),
        freshness_rules=("Brand mentions should refresh within 3 hours.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("brand.mentions", "brand.sentiment"),
    ),
    CmoKpiDefinition(
        key="abm_intent_account_readiness",
        display_name="ABM intent/account readiness",
        description="Share of target accounts with usable intent or readiness signal.",
        formula="intent_ready_accounts / target_accounts * 100",
        required_source_domains=("ABM", "CRM"),
        required_connector_categories=("ABM", "CRM"),
        required_field_mappings=("account_domains", "lifecycle_stages"),
        required_backfill_categories=("ABM", "CRM"),
        refresh_ttl_seconds=24 * 3600,
        unit=PERCENT_UNIT,
        owner_role="growth_lead",
        confidence_rules=("Unavailable ABM connector blocks this KPI until real intent data is configured.",),
        freshness_rules=("ABM intent and CRM accounts should refresh daily.",),
        missing_data_behavior="blocked",
        source_lineage_refs=("abm.intent_accounts", "crm.account_domains"),
    ),
)


def default_cmo_kpi_schema() -> dict[str, Any]:
    return {
        "schema_version": CMO_KPI_SCHEMA_VERSION,
        "kpis": [definition.to_dict() for definition in CMO_KPI_DEFINITIONS],
    }


def cmo_kpi_definition_by_key(key: str) -> dict[str, Any] | None:
    normalized = _normalize_key(key)
    for definition in CMO_KPI_DEFINITIONS:
        if definition.key == normalized:
            return definition.to_dict()
    return None


def evaluate_cmo_kpi(
    kpi_key: str,
    source_data: Mapping[str, Any] | None = None,
    *,
    connector_setup: Iterable[dict[str, Any]] = (),
    data_readiness: Mapping[str, Any] | None = None,
    connector_contracts: Iterable[dict[str, Any]] = (),
    reconciliation_checks: Iterable[dict[str, Any]] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    definition = _definition(kpi_key)
    if definition is None:
        return _unavailable_result(kpi_key, now)
    return _evaluate_definition(
        definition,
        _facts(source_data),
        list(connector_setup),
        data_readiness if isinstance(data_readiness, Mapping) else {},
        list(connector_contracts),
        list(reconciliation_checks),
        _ensure_aware(now) or datetime.now(UTC),
    )


def evaluate_cmo_kpis(
    source_data: Mapping[str, Any] | None = None,
    *,
    connector_setup: Iterable[dict[str, Any]] = (),
    data_readiness: Mapping[str, Any] | None = None,
    connector_contracts: Iterable[dict[str, Any]] = (),
    reconciliation_checks: Iterable[dict[str, Any]] = (),
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    timestamp = _ensure_aware(now) or datetime.now(UTC)
    facts = _facts(source_data)
    setup_rows = list(connector_setup)
    readiness = data_readiness if isinstance(data_readiness, Mapping) else {}
    contract_rows = list(connector_contracts)
    check_rows = list(reconciliation_checks)
    return [
        _evaluate_definition(
            definition,
            facts,
            setup_rows,
            readiness,
            contract_rows,
            check_rows,
            timestamp,
        )
        for definition in CMO_KPI_DEFINITIONS
    ]


def build_unified_cmo_kpi_projection(
    *,
    connector_setup: Iterable[dict[str, Any]],
    data_readiness: Mapping[str, Any],
    connector_contracts: Iterable[dict[str, Any]],
    connector_configs: Iterable[Any] = (),
    source_data: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    setup_rows = list(connector_setup)
    contract_rows = list(connector_contracts)
    config_rows = list(connector_configs)
    facts = collect_cmo_kpi_facts(source_data, config_rows)
    reconciliation_projection = build_cmo_kpi_reconciliation_projection(
        connector_setup=setup_rows,
        data_readiness=data_readiness,
        connector_contracts=contract_rows,
        source_data=facts,
        now=now,
    )
    results = evaluate_cmo_kpis(
        facts,
        connector_setup=setup_rows,
        data_readiness=data_readiness,
        connector_contracts=contract_rows,
        reconciliation_checks=reconciliation_projection["cmo_kpi_reconciliation_checks"],
        now=now,
    )
    return {
        "unified_cmo_kpi_schema": default_cmo_kpi_schema(),
        "unified_cmo_kpi_results": results,
        "unified_cmo_kpi_summary": summarize_cmo_kpi_results(results),
        **reconciliation_projection,
    }


def collect_cmo_kpi_facts(
    source_data: Mapping[str, Any] | None = None,
    connector_configs: Iterable[Any] = (),
) -> dict[str, Any]:
    facts = _kpi_facts_from_configs(connector_configs)
    _merge_facts(facts, _facts(source_data))
    return facts


def summarize_cmo_kpi_results(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(results)
    counts = dict.fromkeys(KPI_STATUSES, 0)
    for item in items:
        status = str(item.get("status") or "")
        if status in counts:
            counts[status] += 1
    readiness = (
        "blocked"
        if counts["blocked"]
        else "degraded"
        if counts["degraded"] or counts["unavailable"]
        else "ready"
    )
    return {
        "schema_version": CMO_KPI_SCHEMA_VERSION,
        "total": len(items),
        **counts,
        "readiness": readiness,
        "needs_action": sum(1 for item in items if item.get("next_action_cta") != "none"),
        "next_action_cta": next(
            (
                str(item.get("next_action_cta"))
                for item in items
                if item.get("next_action_cta") and item.get("next_action_cta") != "none"
            ),
            "none",
        ),
    }


def _evaluate_definition(
    definition: CmoKpiDefinition,
    facts: dict[str, Any],
    connector_setup: list[dict[str, Any]],
    data_readiness: Mapping[str, Any],
    connector_contracts: list[dict[str, Any]],
    reconciliation_checks: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    requirements = _requirements_status(
        definition,
        connector_setup,
        data_readiness,
        connector_contracts,
    )
    formula = _compute_formula(definition.key, facts)
    freshness = _freshness_status(definition, facts, now)
    reconciliation = _reconciliation_impact(definition.key, reconciliation_checks)
    missing_requirements = {
        **requirements["missing_requirements"],
        "fields": formula["missing_fields"],
        "reconciliation": reconciliation["missing_requirements"],
    }

    blocked = bool(
        requirements["blocked_reasons"]
        or reconciliation["blocked_reasons"]
        or (
            formula["missing_fields"]
            and definition.missing_data_behavior != "unavailable_if_ltv_missing"
        )
    )
    unavailable = bool(
        formula["missing_fields"]
        and definition.missing_data_behavior == "unavailable_if_ltv_missing"
    )
    degraded_reasons = [
        *requirements["degraded_reasons"],
        *formula["degraded_reasons"],
        *freshness["degraded_reasons"],
        *reconciliation["degraded_reasons"],
    ]

    if blocked:
        status = "blocked"
        value = None
        confidence = 0.0
        next_action = _next_action(
            missing_requirements,
            freshness,
            reconciliation_cta=reconciliation["next_action_cta"],
        )
    elif unavailable:
        status = "unavailable"
        value = None
        confidence = 0.0
        next_action = "connect_ltv_source"
    elif degraded_reasons:
        status = "degraded"
        value = formula["value"]
        confidence = _confidence(
            requirements,
            freshness,
            formula,
            reconciliation,
            degraded=True,
        )
        next_action = (
            reconciliation["next_action_cta"]
            if reconciliation["next_action_cta"] != "none"
            else _next_action(missing_requirements, freshness, degraded=True)
        )
    else:
        status = "ready"
        value = formula["value"]
        confidence = _confidence(
            requirements,
            freshness,
            formula,
            reconciliation,
            degraded=False,
        )
        next_action = "none"

    source_refs = _source_refs(definition, facts, connector_setup)
    return {
        "kpi_key": definition.key,
        "display_name": definition.display_name,
        "status": status,
        "value": value,
        "unit": definition.unit,
        "confidence": confidence,
        "formula": definition.formula,
        "formula_refs": {
            "schema_version": CMO_KPI_SCHEMA_VERSION,
            "definition_key": definition.key,
            "formula": definition.formula,
            "formula_inputs": formula["input_fields"],
        },
        "source_refs": source_refs,
        "source_lineage_refs": list(definition.source_lineage_refs),
        "audit_refs": _audit_refs(facts, data_readiness),
        "missing_requirements": missing_requirements,
        "blocked_reasons": requirements["blocked_reasons"]
        + reconciliation["blocked_reasons"]
        + (
            [f"Missing source field: {field}" for field in formula["missing_fields"]]
            if blocked
            else []
        ),
        "degraded_reasons": degraded_reasons,
        "reconciliation_status": reconciliation["status"],
        "reconciliation_refs": reconciliation["refs"],
        "reconciliation_confidence_impact": reconciliation["confidence_impact"],
        "freshness_status": freshness["status"],
        "freshness": freshness,
        "last_computed_at": _iso_or_none(facts.get("last_computed_at")) or now.isoformat(),
        "next_action_cta": next_action,
        "owner_role": definition.owner_role,
        "required_field_mappings": list(definition.required_field_mappings),
        "required_connector_categories": list(definition.required_connector_categories),
        "required_backfill_categories": list(definition.required_backfill_categories),
        "missing_data_behavior": definition.missing_data_behavior,
        "zero_denominator": bool(formula.get("zero_denominator")),
    }


def _requirements_status(
    definition: CmoKpiDefinition,
    connector_setup: list[dict[str, Any]],
    data_readiness: Mapping[str, Any],
    connector_contracts: list[dict[str, Any]],
) -> dict[str, Any]:
    blocked: list[str] = []
    degraded: list[str] = []
    missing: dict[str, list[str]] = {"connectors": [], "field_mappings": [], "backfills": []}

    for category in definition.required_connector_categories:
        rows = _setup_rows_for_category(connector_setup, category)
        healthy_rows = [row for row in rows if _health(row) == "healthy"]
        if not rows:
            missing["connectors"].append(category)
            blocked.append(f"Required {category} connector is not configured.")
        elif not healthy_rows:
            health_states = sorted({_health(row) for row in rows})
            if any(state in {"stale", "degraded"} for state in health_states):
                degraded.append(f"Required {category} connector is {', '.join(health_states)}.")
            else:
                missing["connectors"].append(category)
                blocked.append(f"Required {category} connector is not healthy ({', '.join(health_states)}).")

        contract_rows = _contract_rows_for_category(connector_contracts, category)
        if not contract_rows:
            missing["connectors"].append(f"{category}:contract")
            blocked.append(f"Required {category} connector contract is missing.")
        elif any(row.get("blocks_production_kpi_confidence") for row in contract_rows):
            blocked.append(f"Required {category} connector blocks production KPI confidence.")
        elif any(_contract_degraded(row) for row in contract_rows):
            degraded.append(f"Required {category} connector contract is degraded.")
        elif not any(row.get("read_ready") for row in contract_rows):
            missing["connectors"].append(f"{category}:readiness")
            blocked.append(f"Required {category} connector contract is not read-ready.")

    mappings = {
        str(row.get("key") or ""): row
        for row in data_readiness.get("field_mapping_status") or []
        if isinstance(row, Mapping)
    }
    for mapping_key in definition.required_field_mappings:
        row = mappings.get(mapping_key)
        status = str((row or {}).get("status") or "unmapped")
        if status in {"unmapped", "invalid", "blocked"}:
            missing["field_mappings"].append(mapping_key)
            blocked.append(f"Required mapping {mapping_key} is {status.replace('_', ' ')}.")
        elif status in {"partially_mapped", "stale"}:
            degraded.append(f"Required mapping {mapping_key} is {status.replace('_', ' ')}.")

    backfills = [
        row
        for row in data_readiness.get("backfill_status") or []
        if isinstance(row, Mapping)
    ]
    for category in definition.required_backfill_categories:
        configured_sources = _setup_rows_for_category(connector_setup, category)
        category_backfills = [
            row
            for row in backfills
            if str(row.get("category") or "").strip() == category
        ]
        if not configured_sources:
            continue
        if not category_backfills:
            missing["backfills"].append(category)
            blocked.append(f"Required {category} backfill is missing.")
            continue
        statuses = {str(row.get("status") or "") for row in category_backfills}
        if "completed" in statuses:
            continue
        if statuses & {"blocked", "failed"}:
            missing["backfills"].append(category)
            blocked.append(f"Required {category} backfill is blocked or failed.")
        else:
            degraded.append(f"Required {category} backfill is incomplete ({', '.join(sorted(statuses))}).")

    return {
        "blocked_reasons": _unique(blocked),
        "degraded_reasons": _unique(degraded),
        "missing_requirements": {key: _unique(values) for key, values in missing.items()},
        "confidence_impact": _max_confidence_impact(connector_contracts, definition.required_connector_categories),
    }


def _compute_formula(kpi_key: str, facts: dict[str, Any]) -> dict[str, Any]:
    if kpi_key == "cac":
        spend = _number_from(facts, "total_marketing_spend", "marketing_spend", "total_spend", "ad_spend")
        customers = _number_from(facts, "new_customers", "new_customer_count", "customers_acquired")
        return _divide(spend, customers, input_fields=["total_marketing_spend", "new_customers"])
    if kpi_key == "mql":
        return _count_result(_stage_count(facts, "mql"), "mql_count")
    if kpi_key == "sql":
        return _count_result(_stage_count(facts, "sql"), "sql_count")
    if kpi_key == "mql_to_sql_conversion_rate":
        mql = _stage_count(facts, "mql")
        sql = _stage_count(facts, "sql")
        return _percentage(sql, mql, input_fields=["sql_count", "mql_count"], zero_is_zero=True)
    if kpi_key == "roas":
        revenue = _number_from(facts, "attributed_revenue", "marketing_attributed_revenue")
        spend = _number_from(facts, "ad_spend", "total_marketing_spend", "marketing_spend", "total_spend")
        result = _divide(revenue, spend, input_fields=["attributed_revenue", "ad_spend"])
        if _partial_attribution(facts):
            result["degraded_reasons"].append("Attribution coverage is partial.")
        return result
    if kpi_key == "pipeline_contribution":
        value = _number_from(facts, "pipeline_contribution", "marketing_pipeline", "pipeline_value")
        if value is None:
            opportunities = _list_from_value(facts.get("opportunities") or facts.get("pipeline_opportunities"))
            if opportunities:
                value = sum(
                    (_number(item.get("amount")) or 0.0)
                    for item in opportunities
                    if isinstance(item, Mapping)
                )
        return _numeric_result(value, "pipeline_contribution")
    if kpi_key == "conversion_rates_by_funnel_stage":
        return _conversion_rates(facts)
    if kpi_key == "ltv_cac":
        ltv = _number_from(facts, "customer_ltv", "ltv", "average_ltv")
        cac = _number_from(facts, "cac")
        if cac is None:
            cac_result = _compute_formula("cac", facts)
            cac = _number(cac_result.get("value"))
        return _divide(ltv, cac, input_fields=["customer_ltv", "cac"])
    if kpi_key == "experiment_velocity":
        completed = _number_from(facts, "completed_experiments", "experiments_completed")
        period_days = _number_from(facts, "period_days", "experiment_period_days") or 30.0
        if completed is None:
            return _missing_result(["completed_experiments"], ["completed_experiments", "period_days"])
        return _numeric_result((completed / max(period_days, 1.0)) * 30.0, "experiment_velocity")
    if kpi_key == "content_performance":
        sessions = _number_from(facts, "content_sessions", "sessions", "pageviews")
        conversions = _number_from(facts, "content_conversions", "conversions")
        result = _percentage(conversions, sessions, input_fields=["content_conversions", "content_sessions"])
        if result["value"] is not None:
            result["value"] = {
                "conversion_rate": result["value"],
                "sessions": sessions,
                "conversions": conversions,
                "avg_engagement_seconds": _number_from(
                    facts,
                    "content_avg_engagement_seconds",
                    "avg_engagement_seconds",
                ),
            }
        return result
    if kpi_key == "email_performance":
        sent = _number_from(facts, "emails_sent", "email_sent", "sent")
        opened = _number_from(facts, "emails_opened", "email_opens", "opens")
        clicked = _number_from(facts, "emails_clicked", "email_clicks", "clicks")
        unsubscribed = _number_from(facts, "emails_unsubscribed", "unsubscribes")
        missing = [
            name
            for name, value in (
                ("emails_sent", sent),
                ("emails_opened", opened),
                ("emails_clicked", clicked),
            )
            if value is None
        ]
        if missing:
            return _missing_result(
                missing,
                ["emails_sent", "emails_opened", "emails_clicked", "emails_unsubscribed"],
            )
        return {
            "value": {
                "open_rate": _safe_percentage(opened, sent),
                "click_rate": _safe_percentage(clicked, sent),
                "unsubscribe_rate": _safe_percentage(unsubscribed or 0.0, sent),
            },
            "missing_fields": [],
            "degraded_reasons": [],
            "input_fields": ["emails_sent", "emails_opened", "emails_clicked", "emails_unsubscribed"],
            "zero_denominator": sent == 0,
        }
    if kpi_key == "brand_sentiment":
        score = _number_from(facts, "brand_sentiment_score", "sentiment_score")
        if score is not None:
            return _numeric_result(score, "brand_sentiment_score")
        positive = _number_from(facts, "brand_positive_mentions", "positive_mentions")
        negative = _number_from(facts, "brand_negative_mentions", "negative_mentions")
        neutral = _number_from(facts, "brand_neutral_mentions", "neutral_mentions") or 0.0
        missing = [
            name
            for name, value in (
                ("brand_positive_mentions", positive),
                ("brand_negative_mentions", negative),
            )
            if value is None
        ]
        if missing:
            return _missing_result(
                missing,
                [
                    "brand_positive_mentions",
                    "brand_negative_mentions",
                    "brand_neutral_mentions",
                ],
            )
        assert positive is not None
        assert negative is not None
        total = positive + negative + neutral
        return _divide(
            positive - negative,
            total,
            input_fields=[
                "brand_positive_mentions",
                "brand_negative_mentions",
                "brand_neutral_mentions",
            ],
            zero_is_zero=True,
        )
    if kpi_key == "abm_intent_account_readiness":
        ready = _number_from(facts, "intent_ready_accounts", "abm_intent_accounts")
        targets = _number_from(facts, "target_accounts", "target_account_count")
        return _percentage(ready, targets, input_fields=["intent_ready_accounts", "target_accounts"], zero_is_zero=True)
    return _missing_result(["formula"], ["formula"])


def _count_result(value: float | None, field_name: str) -> dict[str, Any]:
    return _numeric_result(value, field_name)


def _numeric_result(value: float | None, field_name: str) -> dict[str, Any]:
    if value is None:
        return _missing_result([field_name], [field_name])
    return {
        "value": _round(value),
        "missing_fields": [],
        "degraded_reasons": [],
        "input_fields": [field_name],
        "zero_denominator": False,
    }


def _divide(
    numerator: float | None,
    denominator: float | None,
    *,
    input_fields: list[str],
    zero_is_zero: bool = False,
) -> dict[str, Any]:
    missing: list[str] = []
    if numerator is None:
        missing.append(input_fields[0])
    if denominator is None:
        missing.append(input_fields[1])
    if missing:
        return _missing_result(missing, input_fields)
    assert numerator is not None
    assert denominator is not None
    if denominator == 0:
        if zero_is_zero:
            return {
                "value": 0.0,
                "missing_fields": [],
                "degraded_reasons": [f"Zero denominator for {input_fields[1]}."],
                "input_fields": input_fields,
                "zero_denominator": True,
            }
        return _missing_result([input_fields[1]], input_fields)
    return {
        "value": _round(numerator / denominator),
        "missing_fields": [],
        "degraded_reasons": [],
        "input_fields": input_fields,
        "zero_denominator": False,
    }


def _percentage(
    numerator: float | None,
    denominator: float | None,
    *,
    input_fields: list[str],
    zero_is_zero: bool = False,
) -> dict[str, Any]:
    result = _divide(numerator, denominator, input_fields=input_fields, zero_is_zero=zero_is_zero)
    if result["value"] is not None:
        result["value"] = _round(float(result["value"]) * 100)
    return result


def _conversion_rates(facts: dict[str, Any]) -> dict[str, Any]:
    counts = _dict_from_value(facts.get("funnel_stage_counts") or facts.get("lifecycle_stage_counts"))
    if not counts:
        return _missing_result(["funnel_stage_counts"], ["funnel_stage_counts"])
    stages = ["visitor", "lead", "mql", "sql", "opportunity", "customer"]
    rates: dict[str, float] = {}
    zero_denominator = False
    for current, next_stage in zip(stages, stages[1:], strict=False):
        current_count = _number(counts.get(current))
        next_count = _number(counts.get(next_stage))
        if current_count is None or next_count is None:
            continue
        if current_count == 0:
            rates[f"{current}_to_{next_stage}"] = 0.0
            zero_denominator = True
        else:
            rates[f"{current}_to_{next_stage}"] = _round((next_count / current_count) * 100)
    if not rates:
        return _missing_result(["funnel_stage_counts"], ["funnel_stage_counts"])
    return {
        "value": rates,
        "missing_fields": [],
        "degraded_reasons": ["One or more funnel stages had a zero denominator."] if zero_denominator else [],
        "input_fields": ["funnel_stage_counts"],
        "zero_denominator": zero_denominator,
    }


def _missing_result(missing_fields: list[str], input_fields: list[str]) -> dict[str, Any]:
    return {
        "value": None,
        "missing_fields": _unique(missing_fields),
        "degraded_reasons": [],
        "input_fields": input_fields,
        "zero_denominator": False,
    }


def _freshness_status(
    definition: CmoKpiDefinition,
    facts: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    last_updated = _parse_datetime(
        facts.get(f"{definition.key}_last_updated_at")
        or facts.get("last_updated_at")
        or facts.get("last_sync_at")
        or facts.get("computed_at")
    )
    if last_updated is None:
        return {
            "status": "unknown",
            "last_updated_at": None,
            "ttl_seconds": definition.refresh_ttl_seconds,
            "age_seconds": None,
            "degraded_reasons": ["Source freshness timestamp is missing."],
        }
    age_seconds = max(int((now - last_updated).total_seconds()), 0)
    if age_seconds > definition.refresh_ttl_seconds:
        return {
            "status": "stale",
            "last_updated_at": last_updated.isoformat(),
            "ttl_seconds": definition.refresh_ttl_seconds,
            "age_seconds": age_seconds,
            "degraded_reasons": ["Source data is stale for this KPI TTL."],
        }
    return {
        "status": "fresh",
        "last_updated_at": last_updated.isoformat(),
        "ttl_seconds": definition.refresh_ttl_seconds,
        "age_seconds": age_seconds,
        "degraded_reasons": [],
    }


def _confidence(
    requirements: dict[str, Any],
    freshness: dict[str, Any],
    formula: dict[str, Any],
    reconciliation: dict[str, Any],
    *,
    degraded: bool,
) -> float:
    confidence = 0.95
    confidence -= float(requirements.get("confidence_impact") or 0.0)
    confidence -= float(reconciliation.get("confidence_impact") or 0.0)
    if degraded:
        confidence -= 0.15
    if freshness["status"] == "stale":
        confidence -= 0.20
    elif freshness["status"] == "unknown":
        confidence -= 0.10
    if formula.get("zero_denominator"):
        confidence -= 0.10
    return max(round(confidence, 3), 0.0)


def _next_action(
    missing_requirements: dict[str, list[str]],
    freshness: dict[str, Any],
    *,
    reconciliation_cta: str = "none",
    degraded: bool = False,
) -> str:
    if reconciliation_cta not in {"", "none"}:
        return reconciliation_cta
    if missing_requirements.get("connectors"):
        return "configure_required_connector"
    if missing_requirements.get("field_mappings"):
        return "fix_required_mapping"
    if missing_requirements.get("backfills"):
        return "complete_backfill"
    if missing_requirements.get("fields"):
        return "provide_kpi_source_data"
    if freshness["status"] == "stale":
        return "refresh_source_data"
    if degraded:
        return "review_degraded_kpi_inputs"
    return "none"


def _reconciliation_impact(
    kpi_key: str,
    reconciliation_checks: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    key = _normalize_key(kpi_key)
    relevant = [
        check
        for check in reconciliation_checks
        if key in {_normalize_key(item) for item in check.get("affected_kpi_keys") or []}
    ]
    blocked: list[str] = []
    degraded: list[str] = []
    refs: list[str] = []
    missing: list[str] = []
    next_action = "none"
    confidence_impact = 0.0
    rank = {"passed": 0, "warning": 1, "unavailable": 1, "failed": 2, "blocked": 3}
    worst_status = "passed"

    for check in relevant:
        status = str(check.get("status") or "unavailable")
        severity = str(check.get("severity") or "")
        check_key = str(check.get("reconciliation_key") or "unknown_reconciliation")
        worst_status = status if rank.get(status, 1) > rank.get(worst_status, 0) else worst_status
        if check.get("decision_audit_ref"):
            refs.append(str(check["decision_audit_ref"]))
        confidence_impact += float(check.get("confidence_impact") or 0.0)
        missing.extend(
            str(item)
            for values in (check.get("missing_requirements") or {}).values()
            for item in values
        )
        if next_action == "none" and check.get("next_action_cta") not in {None, "", "none"}:
            next_action = str(check["next_action_cta"])

        reason = f"KPI reconciliation {check_key} is {status}."
        if status == "blocked" or bool(check.get("blocks_kpi_readiness")):
            blocked.append(reason)
        elif status == "failed" and severity in {"critical", "high"}:
            blocked.append(reason)
        elif status in {"failed", "warning", "unavailable"}:
            degraded.append(reason)

    return {
        "status": worst_status,
        "blocked_reasons": _unique(blocked),
        "degraded_reasons": _unique(degraded),
        "missing_requirements": _unique(missing),
        "refs": _unique(refs),
        "confidence_impact": round(min(confidence_impact, 0.75), 3),
        "next_action_cta": next_action,
    }


def _source_refs(
    definition: CmoKpiDefinition,
    facts: dict[str, Any],
    connector_setup: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs = []
    for item in _list_from_value(facts.get("source_refs") or facts.get("source_lineage_refs")):
        if isinstance(item, Mapping):
            refs.append(dict(item))
        else:
            refs.append({"ref": str(item)})
    categories = set(definition.required_connector_categories)
    for row in connector_setup:
        if str(row.get("category") or "").strip() in categories:
            refs.append(
                {
                    "connector_key": row.get("key"),
                    "connector_name": row.get("name"),
                    "category": row.get("category"),
                    "last_sync_at": row.get("last_sync_at"),
                    "health_status": row.get("health_status"),
                }
            )
    return refs


def _audit_refs(facts: dict[str, Any], data_readiness: Mapping[str, Any]) -> list[str]:
    refs = _string_list(facts.get("audit_refs") or facts.get("decision_audit_refs"))
    readiness_rows = [
        *list(data_readiness.get("field_mapping_status") or []),
        *list(data_readiness.get("backfill_status") or []),
    ]
    for row in readiness_rows:
        if isinstance(row, Mapping) and row.get("decision_audit_ref"):
            refs.append(str(row["decision_audit_ref"]))
    return _unique(refs)


def _kpi_facts_from_configs(connector_configs: Iterable[Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for config in connector_configs:
        payload = _config_dict(config)
        for key in (
            "marketing_kpi_inputs",
            "cmo_kpi_inputs",
            "marketing_kpi_facts",
            "cmo_kpi_facts",
            "kpi_inputs",
        ):
            value = payload.get(key)
            if isinstance(value, Mapping):
                _merge_facts(facts, value)
    return facts


def _merge_facts(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if key in {"source_refs", "source_lineage_refs", "audit_refs", "decision_audit_refs"}:
            target.setdefault(key, [])
            target[key] = [*list(_list_from_value(target[key])), *list(_list_from_value(value))]
        elif key in {"lifecycle_stage_counts", "funnel_stage_counts"} and isinstance(value, Mapping):
            existing = _dict_from_value(target.get(key))
            for stage, count in value.items():
                normalized_stage = _normalize_key(stage)
                existing[normalized_stage] = (
                    (_number(existing.get(normalized_stage)) or 0.0)
                    + (_number(count) or 0.0)
                )
            target[key] = existing
        elif key in {"opportunities", "pipeline_opportunities"}:
            target.setdefault(key, [])
            target[key] = [*list(_list_from_value(target[key])), *list(_list_from_value(value))]
        elif isinstance(value, (int, float)) and isinstance(target.get(key), (int, float)):
            target[key] = float(target[key]) + float(value)
        elif key not in target or target.get(key) in (None, "", [], {}):
            target[key] = value


def _facts(source_data: Mapping[str, Any] | None) -> dict[str, Any]:
    source = source_data if isinstance(source_data, Mapping) else {}
    facts: dict[str, Any] = {}
    for key in ("metrics", "facts", "kpi_inputs", "marketing_kpi_inputs", "cmo_kpi_inputs"):
        value = source.get(key)
        if isinstance(value, Mapping):
            _merge_facts(facts, value)
    _merge_facts(facts, source)
    if "lifecycle_stage_counts" in facts:
        facts["lifecycle_stage_counts"] = {
            _normalize_key(key): _number(value)
            for key, value in _dict_from_value(facts["lifecycle_stage_counts"]).items()
        }
    if "funnel_stage_counts" in facts:
        facts["funnel_stage_counts"] = {
            _normalize_key(key): _number(value)
            for key, value in _dict_from_value(facts["funnel_stage_counts"]).items()
        }
    return facts


def _definition(key: str) -> CmoKpiDefinition | None:
    normalized = _normalize_key(key)
    for definition in CMO_KPI_DEFINITIONS:
        if definition.key == normalized:
            return definition
    return None


def _unavailable_result(kpi_key: str, now: datetime | None) -> dict[str, Any]:
    timestamp = (_ensure_aware(now) or datetime.now(UTC)).isoformat()
    return {
        "kpi_key": _normalize_key(kpi_key),
        "display_name": str(kpi_key),
        "status": "unavailable",
        "value": None,
        "unit": None,
        "confidence": 0.0,
        "formula": None,
        "formula_refs": {},
        "source_refs": [],
        "missing_requirements": {"definition": [_normalize_key(kpi_key)]},
        "freshness_status": "unknown",
        "last_computed_at": timestamp,
        "next_action_cta": "define_kpi",
    }


def _setup_rows_for_category(rows: Iterable[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("category") or "").strip() == category
        and str(row.get("configured_status") or "") != "unconfigured"
    ]


def _contract_rows_for_category(rows: Iterable[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("category") or "").strip() == category
        and str(row.get("configured_status") or "") != "unconfigured"
    ]


def _contract_degraded(row: Mapping[str, Any]) -> bool:
    degraded_mode = row.get("degraded_mode") if isinstance(row.get("degraded_mode"), Mapping) else {}
    return (
        row.get("read_status") == "degraded"
        or str(degraded_mode.get("status") or "") in {"degraded", "blocked"}
        or bool(degraded_mode.get("active"))
    )


def _max_confidence_impact(rows: Iterable[dict[str, Any]], categories: Iterable[str]) -> float:
    category_set = set(categories)
    impacts = []
    for row in rows:
        if str(row.get("category") or "").strip() not in category_set:
            continue
        degraded_mode = row.get("degraded_mode") if isinstance(row.get("degraded_mode"), Mapping) else {}
        impacts.append(float(row.get("confidence_impact") or degraded_mode.get("confidence_impact") or 0.0))
    return max(impacts, default=0.0)


def _stage_count(facts: Mapping[str, Any], stage: str) -> float | None:
    explicit = _number_from(facts, f"{stage}_count", stage)
    if explicit is not None:
        return explicit
    counts = _dict_from_value(facts.get("lifecycle_stage_counts"))
    return _number(counts.get(stage))


def _partial_attribution(facts: Mapping[str, Any]) -> bool:
    status = _normalize_key(facts.get("attribution_status"))
    if status in {"partial", "degraded", "incomplete"}:
        return True
    coverage = _number(facts.get("attribution_coverage"))
    return coverage is not None and coverage < 1.0


def _number_from(facts: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(facts.get(key))
        if value is not None:
            return value
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_percentage(numerator: float | None, denominator: float | None) -> float:
    if numerator is None or denominator is None or denominator == 0:
        return 0.0
    return _round((numerator / denominator) * 100)


def _round(value: float) -> float:
    return round(float(value), 4)


def _dict_from_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_from_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in _list_from_value(value) if str(item).strip()]


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _config_dict(config: Any | None) -> dict[str, Any]:
    if isinstance(config, Mapping):
        return dict(config)
    value = getattr(config, "config", None)
    return dict(value) if isinstance(value, Mapping) else {}


def _health(row: Mapping[str, Any]) -> str:
    return str(row.get("health_status") or "missing").strip().lower()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_aware(parsed)


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso_or_none(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is not None:
        return parsed.isoformat()
    text = str(value or "").strip()
    return text or None


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
