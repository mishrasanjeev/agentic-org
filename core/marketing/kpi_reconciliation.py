"""CMO KPI reconciliation checks.

CMO-7.2 cross-checks source totals before canonical marketing KPIs are treated
as trusted production output. The checks are deterministic projections over
structured source facts and connector readiness. They do not invent source
values or call vendor APIs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from core.marketing.decision_audit import build_cmo_decision_audit_package

CMO_KPI_RECONCILIATION_VERSION = "2026-05-23.cmo-7.2"

RECONCILIATION_STATUSES = ("passed", "warning", "failed", "blocked", "unavailable")

FINANCIAL_KPIS = ("cac", "roas", "pipeline_contribution", "ltv_cac")
ATTRIBUTION_KPIS = (
    "mql",
    "sql",
    "mql_to_sql_conversion_rate",
    "roas",
    "pipeline_contribution",
    "conversion_rates_by_funnel_stage",
)
TIME_BASED_KPIS = (
    "cac",
    "mql",
    "sql",
    "mql_to_sql_conversion_rate",
    "roas",
    "pipeline_contribution",
    "conversion_rates_by_funnel_stage",
    "experiment_velocity",
    "content_performance",
    "email_performance",
    "brand_sentiment",
    "abm_intent_account_readiness",
)

CATEGORY_KPI_KEYS = {
    "Ads": ("cac", "roas", "experiment_velocity", "ltv_cac"),
    "CRM": (
        "cac",
        "mql",
        "sql",
        "mql_to_sql_conversion_rate",
        "roas",
        "pipeline_contribution",
        "conversion_rates_by_funnel_stage",
        "ltv_cac",
        "email_performance",
        "abm_intent_account_readiness",
    ),
    "Analytics": (
        "roas",
        "conversion_rates_by_funnel_stage",
        "experiment_velocity",
        "content_performance",
    ),
    "CMS": ("content_performance", "experiment_velocity"),
    "Email": ("email_performance", "experiment_velocity"),
    "Brand": ("brand_sentiment",),
    "ABM": ("abm_intent_account_readiness",),
    "Finance": ("cac", "roas", "pipeline_contribution", "ltv_cac"),
}


def build_cmo_kpi_reconciliation_projection(
    *,
    connector_setup: Iterable[dict[str, Any]] = (),
    data_readiness: Mapping[str, Any] | None = None,
    connector_contracts: Iterable[dict[str, Any]] = (),
    connector_configs: Iterable[Any] = (),
    source_data: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build all CMO KPI reconciliation checks and a compact summary."""

    timestamp = _ensure_aware(now) or datetime.now(UTC)
    facts = _collect_reconciliation_facts(source_data, connector_configs)
    setup_rows = list(connector_setup)
    contract_rows = list(connector_contracts)
    checks = [
        _paid_spend_totals_check(facts),
        _ad_conversion_crm_attribution_check(facts),
        _ga4_web_crm_lead_check(facts),
        _email_engagement_check(facts),
        _content_traffic_check(facts),
        _abm_account_domain_check(facts),
        _currency_consistency_check(facts),
        _timezone_consistency_check(facts),
        _freshness_and_partial_data_check(setup_rows, contract_rows, timestamp),
    ]
    return {
        "cmo_kpi_reconciliation_version": CMO_KPI_RECONCILIATION_VERSION,
        "cmo_kpi_reconciliation_checks": checks,
        "cmo_kpi_reconciliation_summary": summarize_cmo_kpi_reconciliation(checks),
    }


def summarize_cmo_kpi_reconciliation(checks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(checks)
    counts = dict.fromkeys(RECONCILIATION_STATUSES, 0)
    for item in items:
        status = str(item.get("status") or "")
        if status in counts:
            counts[status] += 1

    if counts["failed"] or counts["blocked"]:
        readiness = "blocked"
    elif counts["warning"] or counts["unavailable"]:
        readiness = "degraded"
    else:
        readiness = "ready"

    affected = sorted(
        {
            str(kpi)
            for item in items
            if item.get("status") in {"failed", "blocked", "warning", "unavailable"}
            for kpi in item.get("affected_kpi_keys") or []
            if str(kpi).strip()
        }
    )
    return {
        "schema_version": CMO_KPI_RECONCILIATION_VERSION,
        "total": len(items),
        **counts,
        "readiness": readiness,
        "affected_kpi_keys": affected,
        "confidence_impact": round(
            min(sum(float(item.get("confidence_impact") or 0.0) for item in items), 0.75),
            3,
        ),
        "freshness_impact": round(
            min(sum(float(item.get("freshness_impact") or 0.0) for item in items), 0.75),
            3,
        ),
        "next_action_cta": next(
            (
                str(item.get("next_action_cta"))
                for item in items
                if item.get("next_action_cta") not in {None, "", "none"}
            ),
            "none",
        ),
    }


def _paid_spend_totals_check(facts: Mapping[str, Any]) -> dict[str, Any]:
    expected = _number_from(
        facts,
        "ad_platform_spend_total",
        "paid_spend_total",
        "total_marketing_spend",
        "ad_spend",
    )
    observed = _number_from(facts, "campaign_level_spend_total", "campaign_spend_total")
    if observed is None:
        observed = _sum_amounts(
            facts.get("campaign_spend_by_channel")
            or facts.get("campaign_spend")
            or facts.get("campaign_level_spend")
        )
    return _numeric_check(
        key="paid_spend_totals_by_channel",
        severity="high",
        affected_kpi_keys=("cac", "roas", "ltv_cac"),
        sources_compared=("ad_platform_spend_total", "campaign_level_spend"),
        expected=expected,
        observed=observed,
        expected_field="ad_platform_spend_total",
        observed_field="campaign_level_spend",
        tolerance_percentage=2.0,
        tolerance_absolute=1.0,
        next_action_cta="resolve_spend_reconciliation",
        failure_blocks=True,
        facts=facts,
    )


def _ad_conversion_crm_attribution_check(facts: Mapping[str, Any]) -> dict[str, Any]:
    expected = _number_from(facts, "ad_platform_conversions", "ad_conversions", "platform_conversions")
    observed = _number_from(
        facts,
        "crm_campaign_attributed_mqls",
        "crm_attributed_mqls",
        "crm_campaign_attributed_conversions",
    )
    if observed is None:
        observed = _stage_count(facts, "mql")
    return _numeric_check(
        key="ad_conversions_vs_crm_attribution",
        severity="medium",
        affected_kpi_keys=ATTRIBUTION_KPIS,
        sources_compared=("ad_platform_conversions", "crm_campaign_attributed_mqls"),
        expected=expected,
        observed=observed,
        expected_field="ad_platform_conversions",
        observed_field="crm_campaign_attributed_mqls",
        tolerance_percentage=20.0,
        tolerance_absolute=5.0,
        next_action_cta="review_campaign_attribution_mapping",
        failure_blocks=False,
        facts=facts,
    )


def _ga4_web_crm_lead_check(facts: Mapping[str, Any]) -> dict[str, Any]:
    expected = _number_from(facts, "ga4_conversion_events", "web_conversion_events", "analytics_conversion_events")
    observed = _number_from(facts, "crm_leads_created", "crm_lead_creations", "lead_count")
    if observed is None:
        observed = _stage_count(facts, "lead")
    return _numeric_check(
        key="ga4_web_conversions_vs_crm_leads",
        severity="medium",
        affected_kpi_keys=(
            "mql",
            "sql",
            "mql_to_sql_conversion_rate",
            "conversion_rates_by_funnel_stage",
            "roas",
        ),
        sources_compared=("ga4_conversion_events", "crm_leads_created"),
        expected=expected,
        observed=observed,
        expected_field="ga4_conversion_events",
        observed_field="crm_leads_created",
        tolerance_percentage=15.0,
        tolerance_absolute=5.0,
        next_action_cta="review_web_to_crm_lead_mapping",
        failure_blocks=False,
        facts=facts,
    )


def _email_engagement_check(facts: Mapping[str, Any]) -> dict[str, Any]:
    pairs = (
        ("emails_sent", "crm_email_sends"),
        ("emails_clicked", "crm_email_clicks"),
        ("emails_unsubscribed", "crm_unsubscribed_contacts"),
    )
    return _multi_numeric_check(
        key="email_engagement_vs_crm_list",
        severity="medium",
        affected_kpi_keys=("email_performance",),
        sources_compared=("email_platform_metrics", "crm_or_list_engagement"),
        field_pairs=pairs,
        facts=facts,
        tolerance_percentage=5.0,
        tolerance_absolute=2.0,
        next_action_cta="review_email_crm_engagement_sync",
        failure_blocks=False,
    )


def _content_traffic_check(facts: Mapping[str, Any]) -> dict[str, Any]:
    expected = _number_from(facts, "wordpress_content_sessions", "cms_content_sessions", "content_cms_sessions")
    observed = _number_from(facts, "ga4_content_sessions", "content_sessions", "analytics_content_sessions")
    return _numeric_check(
        key="content_traffic_vs_ga4",
        severity="medium",
        affected_kpi_keys=("content_performance",),
        sources_compared=("wordpress_content_sessions", "ga4_content_sessions"),
        expected=expected,
        observed=observed,
        expected_field="wordpress_content_sessions",
        observed_field="ga4_content_sessions",
        tolerance_percentage=10.0,
        tolerance_absolute=10.0,
        next_action_cta="review_content_analytics_mapping",
        failure_blocks=False,
        facts=facts,
    )


def _abm_account_domain_check(facts: Mapping[str, Any]) -> dict[str, Any]:
    targets = _domain_set(
        facts.get("abm_target_account_domains")
        or facts.get("target_account_domains")
        or facts.get("target_accounts_domains")
    )
    crm_domains = _domain_set(facts.get("crm_account_domains") or facts.get("account_domains"))
    intent_domains = _domain_set(facts.get("intent_account_domains") or facts.get("abm_intent_account_domains"))

    missing = []
    if not targets:
        missing.append("abm_target_account_domains")
    if not crm_domains:
        missing.append("crm_account_domains")
    if not intent_domains:
        missing.append("intent_account_domains")
    if missing:
        return _blocked_check(
            key="abm_account_domain_consistency",
            severity="high",
            affected_kpi_keys=("abm_intent_account_readiness",),
            sources_compared=("abm_target_accounts", "crm_account_domains", "intent_account_domains"),
            missing_fields=missing,
            next_action_cta="map_abm_and_crm_account_domains",
            facts=facts,
        )

    reconciled = targets & crm_domains & intent_domains
    missing_domains = sorted(targets - reconciled)
    expected = float(len(targets))
    observed = float(len(reconciled))
    result = _numeric_check(
        key="abm_account_domain_consistency",
        severity="high",
        affected_kpi_keys=("abm_intent_account_readiness",),
        sources_compared=("abm_target_accounts", "crm_account_domains", "intent_account_domains"),
        expected=expected,
        observed=observed,
        expected_field="abm_target_account_domains",
        observed_field="crm_and_intent_domain_overlap",
        tolerance_percentage=5.0,
        tolerance_absolute=0.0,
        next_action_cta="resolve_abm_account_domain_mapping",
        failure_blocks=True,
        facts=facts,
    )
    result["missing_requirements"]["domains"] = missing_domains
    if missing_domains and result["status"] == "passed":
        result["status"] = "warning"
        result["severity"] = "medium"
        result["confidence_impact"] = 0.1
    return _attach_audit_ref(result)


def _currency_consistency_check(facts: Mapping[str, Any]) -> dict[str, Any]:
    currencies = _source_values(
        facts,
        aggregate_key="source_currencies",
        explicit_keys=("ads_currency", "crm_currency", "finance_currency", "currency"),
    )
    return _consistency_check(
        key="currency_consistency",
        severity="high",
        affected_kpi_keys=FINANCIAL_KPIS,
        sources_compared=("ads_currency", "crm_currency", "finance_currency"),
        values=currencies,
        missing_field="source_currencies",
        next_action_cta="align_source_currency_mapping",
        failure_blocks=True,
        facts=facts,
    )


def _timezone_consistency_check(facts: Mapping[str, Any]) -> dict[str, Any]:
    timezones = _source_values(
        facts,
        aggregate_key="source_timezones",
        explicit_keys=("ads_timezone", "crm_timezone", "analytics_timezone", "email_timezone", "timezone"),
    )
    return _consistency_check(
        key="timezone_consistency",
        severity="medium",
        affected_kpi_keys=TIME_BASED_KPIS,
        sources_compared=("ads_timezone", "crm_timezone", "analytics_timezone", "email_timezone"),
        values=timezones,
        missing_field="source_timezones",
        next_action_cta="align_source_timezone_mapping",
        failure_blocks=False,
        facts=facts,
    )


def _freshness_and_partial_data_check(
    connector_setup: list[dict[str, Any]],
    connector_contracts: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    configured_setup = [
        row
        for row in connector_setup
        if str(row.get("configured_status") or "") != "unconfigured"
    ]
    if not configured_setup and not connector_contracts:
        return _base_check(
            key="source_freshness_and_partial_data",
            status="unavailable",
            severity="info",
            affected_kpi_keys=(),
            sources_compared=("connector_setup", "connector_contracts"),
            expected_value="configured fresh sources",
            observed_value="no configured source evidence",
            next_action_cta="configure_marketing_connectors",
            missing_requirements={"connectors": ["configured_marketing_source"]},
            confidence_impact=0.0,
            freshness_impact=0.0,
            source_refs=[],
        )

    stale_sources: list[str] = []
    partial_sources: list[str] = []
    affected: set[str] = set()
    refs: list[dict[str, Any]] = []

    for row in configured_setup:
        category = str(row.get("category") or "").strip()
        health = _normalize_key(row.get("health_status"))
        if category in CATEGORY_KPI_KEYS:
            affected.update(CATEGORY_KPI_KEYS[category])
        refs.append(
            {
                "connector_key": row.get("key"),
                "category": category,
                "health_status": row.get("health_status"),
                "last_sync_at": row.get("last_sync_at"),
            }
        )
        if health == "stale":
            stale_sources.append(str(row.get("key") or row.get("name") or category))
        elif health in {"degraded", "partial_data"}:
            partial_sources.append(str(row.get("key") or row.get("name") or category))

    for row in connector_contracts:
        if str(row.get("configured_status") or "") == "unconfigured":
            continue
        category = str(row.get("category") or "").strip()
        if category in CATEGORY_KPI_KEYS:
            affected.update(CATEGORY_KPI_KEYS[category])
        failure_class = _normalize_key(row.get("failure_class"))
        degraded = row.get("degraded_mode") if isinstance(row.get("degraded_mode"), Mapping) else {}
        if failure_class == "stale_data" or row.get("read_status") == "stale":
            stale_sources.append(str(row.get("connector_key") or category))
        if failure_class == "partial_data" or degraded.get("status") == "degraded":
            partial_sources.append(str(row.get("connector_key") or category))

    if stale_sources or partial_sources:
        observed = {
            "stale_sources": sorted(set(stale_sources)),
            "partial_sources": sorted(set(partial_sources)),
            "evaluated_at": now.isoformat(),
        }
        result = _base_check(
            key="source_freshness_and_partial_data",
            status="warning",
            severity="medium",
            affected_kpi_keys=tuple(sorted(affected)),
            sources_compared=("connector_setup", "connector_contracts"),
            expected_value="fresh complete source data",
            observed_value=observed,
            next_action_cta="review_stale_or_partial_sources",
            confidence_impact=0.15 if partial_sources else 0.1,
            freshness_impact=0.2 if stale_sources else 0.0,
            source_refs=refs,
        )
        return _attach_audit_ref(result)

    return _base_check(
        key="source_freshness_and_partial_data",
        status="passed",
        severity="info",
        affected_kpi_keys=tuple(sorted(affected)),
        sources_compared=("connector_setup", "connector_contracts"),
        expected_value="fresh complete source data",
        observed_value="fresh",
        next_action_cta="none",
        source_refs=refs,
    )


def _numeric_check(
    *,
    key: str,
    severity: str,
    affected_kpi_keys: tuple[str, ...],
    sources_compared: tuple[str, ...],
    expected: float | None,
    observed: float | None,
    expected_field: str,
    observed_field: str,
    tolerance_percentage: float,
    tolerance_absolute: float,
    next_action_cta: str,
    failure_blocks: bool,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    missing = []
    if expected is None:
        missing.append(expected_field)
    if observed is None:
        missing.append(observed_field)
    if missing:
        return _blocked_check(
            key=key,
            severity=severity,
            affected_kpi_keys=affected_kpi_keys,
            sources_compared=sources_compared,
            missing_fields=missing,
            next_action_cta=next_action_cta,
            facts=facts,
        )

    assert expected is not None
    assert observed is not None
    delta_absolute = abs(observed - expected)
    delta_percentage = 0.0 if expected == 0 and observed == 0 else _safe_pct(delta_absolute, abs(expected))
    allowed_delta = max(tolerance_absolute, abs(expected) * (tolerance_percentage / 100.0))
    passed = delta_absolute <= allowed_delta
    status = "passed" if passed else "failed"
    result = _base_check(
        key=key,
        status=status,
        severity="info" if passed else severity,
        affected_kpi_keys=affected_kpi_keys,
        sources_compared=sources_compared,
        expected_value=_round(expected),
        observed_value=_round(observed),
        delta_absolute=_round(delta_absolute),
        delta_percentage=_round(delta_percentage),
        tolerance={
            "percentage": tolerance_percentage,
            "absolute": tolerance_absolute,
            "allowed_delta": _round(allowed_delta),
        },
        next_action_cta="none" if passed else next_action_cta,
        confidence_impact=0.0 if passed else _confidence_impact(severity),
        blocks_kpi_readiness=bool(failure_blocks and not passed),
        source_refs=_source_refs(facts, sources_compared),
    )
    return _attach_audit_ref(result)


def _multi_numeric_check(
    *,
    key: str,
    severity: str,
    affected_kpi_keys: tuple[str, ...],
    sources_compared: tuple[str, ...],
    field_pairs: tuple[tuple[str, str], ...],
    facts: Mapping[str, Any],
    tolerance_percentage: float,
    tolerance_absolute: float,
    next_action_cta: str,
    failure_blocks: bool,
) -> dict[str, Any]:
    expected_values: dict[str, float] = {}
    observed_values: dict[str, float] = {}
    missing: list[str] = []
    max_delta_absolute = 0.0
    max_delta_percentage = 0.0
    failed_pair = False
    max_allowed_delta = 0.0

    for expected_field, observed_field in field_pairs:
        expected = _number_from(facts, expected_field)
        observed = _number_from(facts, observed_field)
        if expected is None and observed is None:
            continue
        if expected is None:
            missing.append(expected_field)
            continue
        if observed is None:
            missing.append(observed_field)
            continue
        expected_values[expected_field] = _round(expected)
        observed_values[observed_field] = _round(observed)
        delta_absolute = abs(observed - expected)
        allowed_delta = max(tolerance_absolute, abs(expected) * (tolerance_percentage / 100.0))
        failed_pair = failed_pair or delta_absolute > allowed_delta
        max_allowed_delta = max(max_allowed_delta, allowed_delta)
        max_delta_absolute = max(max_delta_absolute, delta_absolute)
        max_delta_percentage = max(max_delta_percentage, _safe_pct(delta_absolute, abs(expected)))

    if missing or not expected_values:
        if not expected_values:
            missing.extend([expected_field for expected_field, _observed_field in field_pairs])
        return _blocked_check(
            key=key,
            severity=severity,
            affected_kpi_keys=affected_kpi_keys,
            sources_compared=sources_compared,
            missing_fields=missing,
            next_action_cta=next_action_cta,
            facts=facts,
        )

    passed = not failed_pair
    result = _base_check(
        key=key,
        status="passed" if passed else "failed",
        severity="info" if passed else severity,
        affected_kpi_keys=affected_kpi_keys,
        sources_compared=sources_compared,
        expected_value=expected_values,
        observed_value=observed_values,
        delta_absolute=_round(max_delta_absolute),
        delta_percentage=_round(max_delta_percentage),
        tolerance={
            "percentage": tolerance_percentage,
            "absolute": tolerance_absolute,
            "allowed_delta": _round(max_allowed_delta),
        },
        next_action_cta="none" if passed else next_action_cta,
        confidence_impact=0.0 if passed else _confidence_impact(severity),
        blocks_kpi_readiness=bool(failure_blocks and not passed),
        source_refs=_source_refs(facts, sources_compared),
    )
    return _attach_audit_ref(result)


def _consistency_check(
    *,
    key: str,
    severity: str,
    affected_kpi_keys: tuple[str, ...],
    sources_compared: tuple[str, ...],
    values: dict[str, str],
    missing_field: str,
    next_action_cta: str,
    failure_blocks: bool,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    if not values:
        return _blocked_check(
            key=key,
            severity=severity,
            affected_kpi_keys=affected_kpi_keys,
            sources_compared=sources_compared,
            missing_fields=[missing_field],
            next_action_cta=next_action_cta,
            facts=facts,
        )

    normalized_values = sorted(set(values.values()))
    passed = len(normalized_values) == 1
    status = "passed" if passed else "failed"
    if not passed and not failure_blocks:
        status = "warning"
    result = _base_check(
        key=key,
        status=status,
        severity="info" if passed else severity,
        affected_kpi_keys=affected_kpi_keys,
        sources_compared=sources_compared,
        expected_value=normalized_values[0] if normalized_values else None,
        observed_value=values,
        delta_absolute=None,
        delta_percentage=None,
        tolerance={"match": "all_sources_same"},
        next_action_cta="none" if passed else next_action_cta,
        confidence_impact=0.0 if passed else _confidence_impact(severity),
        blocks_kpi_readiness=bool(failure_blocks and not passed),
        source_refs=_source_refs(facts, sources_compared),
    )
    return _attach_audit_ref(result)


def _blocked_check(
    *,
    key: str,
    severity: str,
    affected_kpi_keys: tuple[str, ...],
    sources_compared: tuple[str, ...],
    missing_fields: list[str],
    next_action_cta: str,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    result = _base_check(
        key=key,
        status="blocked",
        severity=severity,
        affected_kpi_keys=affected_kpi_keys,
        sources_compared=sources_compared,
        expected_value=None,
        observed_value=None,
        next_action_cta=next_action_cta,
        missing_requirements={"fields": _unique(missing_fields)},
        confidence_impact=_confidence_impact(severity),
        blocks_kpi_readiness=True,
        source_refs=_source_refs(facts, sources_compared),
    )
    return _attach_audit_ref(result)


def _base_check(
    *,
    key: str,
    status: str,
    severity: str,
    affected_kpi_keys: tuple[str, ...],
    sources_compared: tuple[str, ...],
    expected_value: Any,
    observed_value: Any,
    next_action_cta: str,
    delta_absolute: float | None = None,
    delta_percentage: float | None = None,
    tolerance: Mapping[str, Any] | None = None,
    confidence_impact: float = 0.0,
    freshness_impact: float = 0.0,
    missing_requirements: Mapping[str, list[str]] | None = None,
    blocks_kpi_readiness: bool = False,
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "reconciliation_key": key,
        "status": status,
        "severity": severity,
        "affected_kpi_keys": list(affected_kpi_keys),
        "sources_compared": list(sources_compared),
        "expected_value": expected_value,
        "observed_value": observed_value,
        "delta_absolute": delta_absolute,
        "delta_percentage": delta_percentage,
        "tolerance": dict(tolerance or {}),
        "confidence_impact": round(float(confidence_impact), 3),
        "freshness_impact": round(float(freshness_impact), 3),
        "source_refs": source_refs or [],
        "missing_requirements": {
            key: _unique(values)
            for key, values in dict(missing_requirements or {}).items()
        },
        "next_action_cta": next_action_cta,
        "decision_audit_ref": None,
        "blocks_kpi_readiness": blocks_kpi_readiness,
    }


def _attach_audit_ref(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") == "passed":
        return result
    audit = build_cmo_decision_audit_package(
        {
            "event_type": "policy_decision",
            "decision_type": "kpi_reconciliation",
            "action": result.get("reconciliation_key"),
            "capability": "cmo_kpi_reconciliation",
            "source_refs": result.get("source_refs") or [],
            "rationale": (
                f"KPI reconciliation {result.get('reconciliation_key')} "
                f"returned {result.get('status')}."
            ),
            "risk_flags": [
                str(result.get("status")),
                str(result.get("severity")),
                "kpi_reconciliation",
            ],
            "confidence": max(1.0 - float(result.get("confidence_impact") or 0.0), 0.0),
            "final_outcome": result.get("status"),
            "actor_type": "system",
            "input_snapshot": {
                "expected_value": result.get("expected_value"),
                "observed_value": result.get("observed_value"),
                "delta_absolute": result.get("delta_absolute"),
                "delta_percentage": result.get("delta_percentage"),
                "tolerance": result.get("tolerance"),
            },
        }
    )
    result["decision_audit_ref"] = audit["audit_reference"]
    return result


def _collect_reconciliation_facts(
    source_data: Mapping[str, Any] | None,
    connector_configs: Iterable[Any],
) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for config in connector_configs:
        payload = _config_dict(config)
        for key in (
            "marketing_kpi_inputs",
            "cmo_kpi_inputs",
            "marketing_kpi_facts",
            "cmo_kpi_facts",
            "kpi_inputs",
            "marketing_reconciliation",
            "cmo_kpi_reconciliation",
            "reconciliation_inputs",
        ):
            value = payload.get(key)
            if isinstance(value, Mapping):
                _merge_facts(facts, value)
    source = source_data if isinstance(source_data, Mapping) else {}
    for key in (
        "metrics",
        "facts",
        "kpi_inputs",
        "marketing_kpi_inputs",
        "cmo_kpi_inputs",
        "marketing_reconciliation",
        "cmo_kpi_reconciliation",
        "reconciliation_inputs",
    ):
        value = source.get(key)
        if isinstance(value, Mapping):
            _merge_facts(facts, value)
    _merge_facts(facts, source)
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
        elif isinstance(value, (int, float)) and isinstance(target.get(key), (int, float)):
            target[key] = float(target[key]) + float(value)
        elif key not in target or target.get(key) in (None, "", [], {}):
            target[key] = value


def _sum_amounts(value: Any) -> float | None:
    if isinstance(value, Mapping):
        amounts = [_number(item) for item in value.values()]
        amounts = [item for item in amounts if item is not None]
        return sum(amounts) if amounts else None
    items = _list_from_value(value)
    if not items:
        return None
    amounts = []
    for item in items:
        if isinstance(item, Mapping):
            amount = _number(
                item.get("amount")
                or item.get("spend")
                or item.get("cost")
                or item.get("value")
            )
        else:
            amount = _number(item)
        if amount is not None:
            amounts.append(amount)
    return sum(amounts) if amounts else None


def _stage_count(facts: Mapping[str, Any], stage: str) -> float | None:
    explicit = _number_from(facts, f"{stage}_count", stage)
    if explicit is not None:
        return explicit
    counts = _dict_from_value(facts.get("lifecycle_stage_counts"))
    return _number(counts.get(stage))


def _source_values(
    facts: Mapping[str, Any],
    *,
    aggregate_key: str,
    explicit_keys: tuple[str, ...],
) -> dict[str, str]:
    values: dict[str, str] = {}
    aggregate = facts.get(aggregate_key)
    if isinstance(aggregate, Mapping):
        for source, value in aggregate.items():
            text = str(value or "").strip()
            if text:
                values[str(source)] = text.upper() if "currencies" in aggregate_key else text
    elif isinstance(aggregate, (list, tuple, set)):
        for index, value in enumerate(aggregate):
            text = str(value or "").strip()
            if text:
                values[f"{aggregate_key}_{index}"] = text.upper() if "currencies" in aggregate_key else text
    for key in explicit_keys:
        text = str(facts.get(key) or "").strip()
        if text:
            values[key] = text.upper() if "currency" in key else text
    return values


def _domain_set(value: Any) -> set[str]:
    domains: set[str] = set()
    for item in _list_from_value(value):
        if isinstance(item, Mapping):
            raw = (
                item.get("domain")
                or item.get("account_domain")
                or item.get("website")
                or item.get("value")
            )
        else:
            raw = item
        domain = str(raw or "").strip().lower()
        if domain:
            domains.add(domain.removeprefix("https://").removeprefix("http://").strip("/"))
    return domains


def _source_refs(facts: Mapping[str, Any], sources_compared: tuple[str, ...]) -> list[dict[str, Any]]:
    refs = []
    for item in _list_from_value(facts.get("source_refs") or facts.get("source_lineage_refs")):
        if isinstance(item, Mapping):
            refs.append(dict(item))
        else:
            refs.append({"ref": str(item)})
    for source in sources_compared:
        refs.append({"source": source})
    return refs


def _confidence_impact(severity: str) -> float:
    return {
        "critical": 0.35,
        "high": 0.25,
        "medium": 0.15,
        "low": 0.08,
        "info": 0.0,
    }.get(severity, 0.1)


def _safe_pct(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0 if numerator == 0 else 100.0
    return (numerator / denominator) * 100.0


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


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
