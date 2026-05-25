"""CMO KPI drill-down and data-lineage projections.

CMO-8.2 turns unified KPI results into operator-facing explanations. The
projection is deterministic and intentionally lightweight: it does not persist
KPI history or infer production lineage from demo, sample, mock, or stub data.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from core.marketing.kpi_schema import default_cmo_kpi_schema

CMO_KPI_DRILLDOWN_VERSION = "2026-05-23.cmo-8.2"

DRILLDOWN_STATUSES = ("ready", "degraded", "blocked", "unavailable")
DEMO_SOURCE_MARKERS = {"demo", "sample", "mock", "stub", "hardcoded", "fallback", "test_double"}

FORMULA_INPUT_ALIASES: dict[str, tuple[str, ...]] = {
    "total_marketing_spend": ("total_marketing_spend", "marketing_spend", "total_spend", "ad_spend"),
    "new_customers": ("new_customers", "new_customer_count", "customers_acquired"),
    "mql_count": ("mql_count", "mql"),
    "sql_count": ("sql_count", "sql"),
    "attributed_revenue": ("attributed_revenue", "marketing_attributed_revenue"),
    "ad_spend": ("ad_spend", "total_marketing_spend", "marketing_spend", "total_spend"),
    "pipeline_contribution": ("pipeline_contribution", "marketing_pipeline", "pipeline_value", "opportunities"),
    "customer_ltv": ("customer_ltv", "ltv", "average_ltv"),
    "cac": ("cac",),
    "completed_experiments": ("completed_experiments", "experiments_completed"),
    "period_days": ("period_days", "experiment_period_days"),
    "content_conversions": ("content_conversions", "conversions"),
    "content_sessions": ("content_sessions", "sessions", "pageviews"),
    "emails_sent": ("emails_sent", "email_sent", "sent"),
    "emails_opened": ("emails_opened", "email_opens", "opens"),
    "emails_clicked": ("emails_clicked", "email_clicks", "clicks"),
    "emails_unsubscribed": ("emails_unsubscribed", "unsubscribes"),
    "brand_sentiment_score": ("brand_sentiment_score", "sentiment_score"),
    "brand_positive_mentions": ("brand_positive_mentions", "positive_mentions"),
    "brand_negative_mentions": ("brand_negative_mentions", "negative_mentions"),
    "brand_neutral_mentions": ("brand_neutral_mentions", "neutral_mentions"),
    "intent_ready_accounts": ("intent_ready_accounts", "abm_intent_accounts"),
    "target_accounts": ("target_accounts", "target_account_count"),
    "funnel_stage_counts": ("funnel_stage_counts", "lifecycle_stage_counts"),
}

CTA_LABELS = {
    "none": "No Action",
    "configure_required_connector": "Configure Connector",
    "fix_required_mapping": "Fix Mapping",
    "complete_backfill": "Complete Backfill",
    "provide_kpi_source_data": "Provide Source Data",
    "refresh_source_data": "Refresh Source Data",
    "review_degraded_kpi_inputs": "Review KPI Inputs",
    "resolve_spend_reconciliation": "Resolve Spend Reconciliation",
    "review_campaign_attribution_mapping": "Review Attribution Mapping",
    "review_web_to_crm_lead_mapping": "Review Web-to-CRM Mapping",
    "resolve_reconciliation": "Resolve Reconciliation",
    "review_kpi_readiness": "Review KPI Readiness",
    "connect_ltv_source": "Connect LTV Source",
    "connect_real_marketing_sources": "Connect Real Sources",
}

CTA_PATHS = {
    "none": "/dashboard/cmo",
    "configure_required_connector": "/dashboard/connectors",
    "fix_required_mapping": "/dashboard/connectors",
    "complete_backfill": "/dashboard/connectors",
    "provide_kpi_source_data": "/dashboard/connectors",
    "refresh_source_data": "/dashboard/connectors",
    "review_degraded_kpi_inputs": "/dashboard/cmo?panel=kpi-lineage",
    "resolve_spend_reconciliation": "/dashboard/cmo?panel=reconciliation",
    "review_campaign_attribution_mapping": "/dashboard/cmo?panel=reconciliation",
    "review_web_to_crm_lead_mapping": "/dashboard/cmo?panel=reconciliation",
    "resolve_reconciliation": "/dashboard/cmo?panel=reconciliation",
    "review_kpi_readiness": "/dashboard/cmo?panel=kpi-lineage",
    "connect_ltv_source": "/dashboard/connectors",
    "connect_real_marketing_sources": "/dashboard/connectors",
}


def build_cmo_kpi_drilldown_projection(
    *,
    kpi_schema: Mapping[str, Any] | None = None,
    kpi_results: Iterable[Mapping[str, Any]] = (),
    reconciliation_checks: Iterable[Mapping[str, Any]] = (),
    connector_setup: Iterable[Mapping[str, Any]] = (),
    data_readiness: Mapping[str, Any] | None = None,
    connector_contracts: Iterable[Mapping[str, Any]] = (),
    work_queue: Iterable[Mapping[str, Any]] = (),
    report_quality_gates: Iterable[Mapping[str, Any]] = (),
    source_data: Mapping[str, Any] | None = None,
    source_context: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build KPI drill-down rows and summary counts."""

    timestamp = _ensure_aware(now) or datetime.now(UTC)
    schema = kpi_schema if isinstance(kpi_schema, Mapping) else default_cmo_kpi_schema()
    definitions = _definitions_by_key(schema)
    results = _results_by_key(kpi_results)
    checks = _dicts(reconciliation_checks)
    setup_rows = _dicts(connector_setup)
    contract_rows = _dicts(connector_contracts)
    readiness = data_readiness if isinstance(data_readiness, Mapping) else {}
    queue_rows = _dicts(work_queue)
    report_rows = _dicts(report_quality_gates)
    facts = _facts(source_data)
    source = source_context if isinstance(source_context, Mapping) else {}
    demo_or_sample = (
        _demo_or_sample_source(source)
        or _demo_or_sample_source(facts)
        or _has_mock_contract(contract_rows)
    )

    drilldowns = [
        _build_drilldown(
            definition,
            results.get(key),
            checks,
            setup_rows,
            readiness,
            contract_rows,
            queue_rows,
            report_rows,
            facts,
            timestamp,
            demo_or_sample=demo_or_sample,
        )
        for key, definition in definitions.items()
    ]

    return {
        "cmo_kpi_drilldown_version": CMO_KPI_DRILLDOWN_VERSION,
        "cmo_kpi_drilldowns": drilldowns,
        "cmo_kpi_drilldown_summary": summarize_cmo_kpi_drilldowns(drilldowns),
    }


def summarize_cmo_kpi_drilldowns(drilldowns: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = [dict(item) for item in drilldowns if isinstance(item, Mapping)]
    counts = dict.fromkeys(DRILLDOWN_STATUSES, 0)
    lineage_blocked = 0
    for item in items:
        status = str(item.get("status") or "unavailable")
        if status in counts:
            counts[status] += 1
        if item.get("production_lineage_status") == "blocked":
            lineage_blocked += 1
    readiness = "blocked" if counts["blocked"] or lineage_blocked else "degraded" if counts["degraded"] else "ready"
    return {
        "schema_version": CMO_KPI_DRILLDOWN_VERSION,
        "total": len(items),
        **counts,
        "lineage_blocked": lineage_blocked,
        "readiness": readiness,
        "needs_action": sum(1 for item in items if _cta_key(item.get("next_action_cta")) != "none"),
        "next_action_cta": next(
            (
                item.get("next_action_cta")
                for item in items
                if _cta_key(item.get("next_action_cta")) != "none"
            ),
            _cta("none"),
        ),
    }


def _build_drilldown(
    definition: Mapping[str, Any],
    result: Mapping[str, Any] | None,
    reconciliation_checks: list[dict[str, Any]],
    connector_setup: list[dict[str, Any]],
    data_readiness: Mapping[str, Any],
    connector_contracts: list[dict[str, Any]],
    work_queue: list[dict[str, Any]],
    report_quality_gates: list[dict[str, Any]],
    facts: Mapping[str, Any],
    timestamp: datetime,
    *,
    demo_or_sample: bool,
) -> dict[str, Any]:
    key = _normalize_key(definition.get("key"))
    row = result if isinstance(result, Mapping) else {}
    current_status = _status(row.get("status"))
    missing_requirements = _missing_requirements(row.get("missing_requirements"))
    blocked_reasons = _string_list(row.get("blocked_reasons"))
    degraded_reasons = _string_list(row.get("degraded_reasons"))
    source_refs = _source_refs(row.get("source_refs")) if not demo_or_sample else []
    connector_refs = _connector_refs(definition, connector_setup, connector_contracts)
    reconciliation = _reconciliation_for_kpi(key, reconciliation_checks)
    report_refs = _report_gate_refs(key, report_quality_gates)
    work_item_ids = _work_item_ids(key, work_queue)
    audit_refs = _unique(
        [
            *_string_list(row.get("audit_refs") or row.get("decision_audit_refs")),
            *_string_list(row.get("reconciliation_refs")),
            *[
                str(check.get("decision_audit_ref"))
                for check in reconciliation
                if check.get("decision_audit_ref")
            ],
            *[
                str(gate.get("decision_audit_ref"))
                for gate in report_quality_gates
                if key in _kpi_refs_from_report_gate(gate) and gate.get("decision_audit_ref")
            ],
        ]
    )
    if demo_or_sample:
        missing_requirements.setdefault("source_data", [])
        missing_requirements["source_data"].append("real_production_lineage")
        blocked_reasons.append("Demo, mock, stub, hardcoded, or sample KPI inputs are not production lineage proof.")
    next_action_key = _next_action(row, missing_requirements, demo_or_sample)

    return {
        "drilldown_id": f"cmo_kpi_drilldown:{key}",
        "kpi_key": key,
        "display_name": str(definition.get("display_name") or row.get("display_name") or key),
        "description": str(definition.get("description") or ""),
        "status": current_status,
        "value": row.get("value"),
        "unit": row.get("unit") or definition.get("unit"),
        "confidence": float(row.get("confidence") or 0.0),
        "formula": row.get("formula") or definition.get("formula"),
        "formula_refs": row.get("formula_refs") or _formula_refs(definition),
        "formula_inputs": _formula_inputs(row, facts),
        "required_source_domains": list(definition.get("required_source_domains") or []),
        "required_connector_categories": list(definition.get("required_connector_categories") or []),
        "required_field_mappings": list(definition.get("required_field_mappings") or []),
        "required_backfill_categories": list(definition.get("required_backfill_categories") or []),
        "source_refs": source_refs,
        "connector_refs": connector_refs,
        "field_mappings_used": _field_mapping_rows(definition, data_readiness),
        "backfill_state": _backfill_rows(definition, data_readiness),
        "reconciliation_checks": reconciliation,
        "freshness_status": row.get("freshness_status") or "unknown",
        "freshness": row.get("freshness") or {},
        "confidence_rules": list(definition.get("confidence_rules") or []),
        "confidence_impact_reasons": _confidence_reasons(row, reconciliation, demo_or_sample),
        "missing_requirements": {name: _unique(values) for name, values in missing_requirements.items()},
        "blocked_reasons": _unique(blocked_reasons),
        "degraded_reasons": _unique(degraded_reasons),
        "related_work_queue_item_ids": work_item_ids,
        "related_report_gate_ids": report_refs,
        "policy_refs": _policy_refs(report_quality_gates, key),
        "audit_refs": audit_refs,
        "owner_role": row.get("owner_role") or definition.get("owner_role") or "marketing_ops",
        "next_action_cta": _cta(next_action_key),
        "production_lineage_status": "blocked" if demo_or_sample else "ready",
        "production_lineage_ready": not demo_or_sample,
        "last_computed_at": row.get("last_computed_at") or timestamp.isoformat(),
    }


def _definitions_by_key(schema: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    definitions = {}
    for item in _dicts(schema.get("kpis")):
        key = _normalize_key(item.get("key"))
        if key:
            definitions[key] = item
    return definitions


def _results_by_key(results: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = {}
    for item in _dicts(results):
        key = _normalize_key(item.get("kpi_key") or item.get("key"))
        if key:
            rows[key] = item
    return rows


def _formula_inputs(result: Mapping[str, Any], facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = result.get("formula_refs") if isinstance(result.get("formula_refs"), Mapping) else {}
    input_names = _string_list(refs.get("formula_inputs"))
    if not input_names:
        input_names = _string_list(result.get("required_formula_inputs"))
    return [_resolved_formula_input(name, facts, result) for name in input_names]


def _resolved_formula_input(name: str, facts: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    aliases = FORMULA_INPUT_ALIASES.get(name, (name,))
    for alias in aliases:
        value = _resolve_fact(alias, facts, result)
        if value is not None:
            return {"name": name, "source_key": alias, "value": value, "resolved": True}
    return {"name": name, "source_key": aliases[0] if aliases else name, "value": None, "resolved": False}


def _resolve_fact(alias: str, facts: Mapping[str, Any], result: Mapping[str, Any]) -> Any:
    if alias in {"mql_count", "mql"}:
        return _stage_count(facts, "mql")
    if alias in {"sql_count", "sql"}:
        return _stage_count(facts, "sql")
    if alias == "opportunities":
        opportunities = _dicts(facts.get("opportunities") or facts.get("pipeline_opportunities"))
        if opportunities:
            return sum(float(item.get("amount") or 0.0) for item in opportunities)
    if alias == "cac" and result.get("kpi_key") == "ltv_cac":
        return None
    if alias in facts:
        return facts[alias]
    return None


def _stage_count(facts: Mapping[str, Any], stage: str) -> float | None:
    explicit = facts.get(f"{stage}_count") or facts.get(stage)
    if explicit is not None:
        return _number(explicit)
    counts = facts.get("lifecycle_stage_counts")
    if isinstance(counts, Mapping):
        return _number(counts.get(stage))
    return None


def _connector_refs(
    definition: Mapping[str, Any],
    connector_setup: list[dict[str, Any]],
    connector_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    categories = {str(item) for item in definition.get("required_connector_categories") or []}
    refs = []
    for row in connector_setup:
        if str(row.get("category") or "") in categories:
            refs.append(
                {
                    "type": "connector_setup",
                    "connector_key": row.get("key") or row.get("connector_key"),
                    "connector_name": row.get("name"),
                    "category": row.get("category"),
                    "configured_status": row.get("configured_status"),
                    "health_status": row.get("health_status"),
                    "last_sync_at": row.get("last_sync_at"),
                    "account_id": row.get("account_id") or row.get("workspace_id"),
                }
            )
    for row in connector_contracts:
        if str(row.get("category") or "") in categories:
            refs.append(
                {
                    "type": "connector_contract",
                    "connector_key": row.get("connector_key") or row.get("key"),
                    "category": row.get("category"),
                    "read_status": row.get("read_status"),
                    "write_status": row.get("write_status"),
                    "health_status": row.get("health_status"),
                    "contract_state": row.get("contract_state"),
                    "mock_or_test_double": bool(row.get("mock_or_test_double")),
                }
            )
    return _unique_refs(refs)


def _field_mapping_rows(
    definition: Mapping[str, Any],
    data_readiness: Mapping[str, Any],
) -> list[dict[str, Any]]:
    required = {str(item) for item in definition.get("required_field_mappings") or []}
    rows = []
    for row in _dicts(data_readiness.get("field_mapping_status")):
        key = str(row.get("key") or "")
        if key not in required:
            continue
        rows.append(
            {
                "key": key,
                "name": row.get("name") or key,
                "status": row.get("status") or "unmapped",
                "sources": row.get("sources") or row.get("source_categories") or [],
                "missing_fields": row.get("missing_fields") or [],
                "blocking_reason": row.get("blocking_reason"),
                "audit_ref": row.get("decision_audit_ref") or row.get("audit_reference"),
            }
        )
    return rows


def _backfill_rows(
    definition: Mapping[str, Any],
    data_readiness: Mapping[str, Any],
) -> list[dict[str, Any]]:
    required = {str(item) for item in definition.get("required_backfill_categories") or []}
    rows = []
    for row in _dicts(data_readiness.get("backfill_status")):
        category = str(row.get("category") or "")
        if category not in required:
            continue
        rows.append(
            {
                "source_connector_key": row.get("source_connector_key"),
                "source_name": row.get("source_name"),
                "category": category,
                "status": row.get("status") or "not_started",
                "requested_start": row.get("requested_start"),
                "requested_end": row.get("requested_end"),
                "last_run_at": row.get("last_run_at"),
                "blocking_reason": row.get("blocking_reason"),
                "audit_ref": row.get("decision_audit_ref") or row.get("audit_reference"),
            }
        )
    return rows


def _reconciliation_for_kpi(kpi_key: str, checks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for check in _dicts(checks):
        affected = {_normalize_key(item) for item in check.get("affected_kpi_keys") or []}
        if kpi_key not in affected:
            continue
        rows.append(
            {
                "reconciliation_key": check.get("reconciliation_key"),
                "status": check.get("status"),
                "severity": check.get("severity"),
                "sources_compared": check.get("sources_compared") or [],
                "expected_value": check.get("expected_value"),
                "observed_value": check.get("observed_value"),
                "delta_absolute": check.get("delta_absolute"),
                "delta_percentage": check.get("delta_percentage"),
                "tolerance": check.get("tolerance") or {},
                "confidence_impact": float(check.get("confidence_impact") or 0.0),
                "freshness_impact": float(check.get("freshness_impact") or 0.0),
                "source_refs": _source_refs(check.get("source_refs")),
                "missing_requirements": check.get("missing_requirements") or {},
                "next_action_cta": _cta(str(check.get("next_action_cta") or "none")),
                "decision_audit_ref": check.get("decision_audit_ref"),
            }
        )
    return rows


def _report_gate_refs(kpi_key: str, gates: Iterable[Mapping[str, Any]]) -> list[str]:
    refs = []
    for gate in _dicts(gates):
        if kpi_key in _kpi_refs_from_report_gate(gate):
            ref = str(gate.get("report_key") or gate.get("report_type") or "")
            if ref:
                refs.append(ref)
    return _unique(refs)


def _kpi_refs_from_report_gate(gate: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in (
        "required_kpi_keys",
        "optional_kpi_keys",
        "blocked_kpi_keys",
        "degraded_kpi_keys",
    ):
        refs.update(_normalize_key(item) for item in gate.get(key) or [])
    return refs


def _work_item_ids(kpi_key: str, work_queue: Iterable[Mapping[str, Any]]) -> list[str]:
    ids = []
    for item in _dicts(work_queue):
        affected = _normalize_key(item.get("affected_kpi"))
        affected_parts = {_normalize_key(part) for part in str(item.get("affected_kpi") or "").split(",")}
        if affected == kpi_key or kpi_key in affected_parts:
            item_id = str(item.get("item_id") or "")
            if item_id:
                ids.append(item_id)
    return _unique(ids)


def _policy_refs(gates: Iterable[Mapping[str, Any]], kpi_key: str) -> list[str]:
    refs = []
    for gate in _dicts(gates):
        if kpi_key not in _kpi_refs_from_report_gate(gate):
            continue
        refs.extend(_string_list(gate.get("required_approval_refs")))
        refs.extend(_string_list(gate.get("required_escalation_refs")))
    return _unique(refs)


def _confidence_reasons(
    result: Mapping[str, Any],
    reconciliation: Iterable[Mapping[str, Any]],
    demo_or_sample: bool,
) -> list[str]:
    reasons = [
        *_string_list(result.get("blocked_reasons")),
        *_string_list(result.get("degraded_reasons")),
    ]
    freshness = result.get("freshness") if isinstance(result.get("freshness"), Mapping) else {}
    reasons.extend(_string_list(freshness.get("degraded_reasons")))
    for check in reconciliation:
        status = str(check.get("status") or "")
        if status not in {"passed", "info"}:
            reasons.append(f"Reconciliation {check.get('reconciliation_key')} is {status}.")
    if demo_or_sample:
        reasons.append("Demo/mock/sample lineage blocks production confidence.")
    return _unique(reasons)


def _next_action(
    result: Mapping[str, Any],
    missing_requirements: Mapping[str, list[str]],
    demo_or_sample: bool,
) -> str:
    if demo_or_sample:
        return "connect_real_marketing_sources"
    action = str(result.get("next_action_cta") or "none")
    if action != "none":
        return action
    if missing_requirements.get("connectors"):
        return "configure_required_connector"
    if missing_requirements.get("field_mappings"):
        return "fix_required_mapping"
    if missing_requirements.get("backfills"):
        return "complete_backfill"
    if missing_requirements.get("fields"):
        return "provide_kpi_source_data"
    return "none"


def _formula_refs(definition: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": str(definition.get("schema_version") or ""),
        "definition_key": definition.get("key"),
        "formula": definition.get("formula"),
        "formula_inputs": [],
    }


def _missing_requirements(value: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not isinstance(value, Mapping):
        return result
    for key, values in value.items():
        result[str(key)] = _string_list(values)
    return result


def _cta(value: Any) -> dict[str, str]:
    key = _cta_key(value)
    return {
        "action_key": key,
        "label": CTA_LABELS.get(key, key.replace("_", " ").title()),
        "path": CTA_PATHS.get(key, "/dashboard/cmo"),
    }


def _cta_key(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("action_key") or value.get("key") or "none")
    return str(value or "none")


def _facts(source_data: Mapping[str, Any] | None) -> dict[str, Any]:
    source = source_data if isinstance(source_data, Mapping) else {}
    facts: dict[str, Any] = {}
    for key in ("metrics", "facts", "kpi_inputs", "marketing_kpi_inputs", "cmo_kpi_inputs"):
        value = source.get(key)
        if isinstance(value, Mapping):
            facts.update(dict(value))
    facts.update(dict(source))
    return facts


def _source_refs(value: Any) -> list[dict[str, Any]]:
    refs = []
    for item in _list_from_value(value):
        if isinstance(item, Mapping):
            refs.append(dict(item))
        elif str(item).strip():
            refs.append({"ref": str(item)})
    return _unique_refs(refs)


def _demo_or_sample_source(source: Mapping[str, Any]) -> bool:
    if not isinstance(source, Mapping):
        return False
    if source.get("demo") or source.get("production_data_blocked"):
        return True
    marker_values = [
        source.get("source"),
        source.get("data_source"),
        source.get("lineage_source"),
        source.get("kpi_source"),
    ]
    return any(_normalize_key(value) in DEMO_SOURCE_MARKERS for value in marker_values)


def _has_mock_contract(rows: Iterable[Mapping[str, Any]]) -> bool:
    return any(bool(row.get("mock_or_test_double")) for row in rows)


def _status(value: Any) -> str:
    normalized = _normalize_key(value)
    return normalized if normalized in DRILLDOWN_STATUSES else "unavailable"


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dicts(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, Iterable) and not isinstance(value, str):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _list_from_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    return _unique(str(item) for item in _list_from_value(value) if str(item).strip())


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _unique_refs(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for value in values:
        marker = repr(sorted(value.items()))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
