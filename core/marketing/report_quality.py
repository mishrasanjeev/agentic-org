"""CMO report quality gates.

CMO-7.3 turns KPI readiness and reconciliation into report-level decisions.
The gates are deterministic projections over already-computed CMO readiness
state; they do not render reports, persist report history, or invent values.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.marketing.decision_audit import build_cmo_decision_audit_package
from core.marketing.escalation_matrix import evaluate_marketing_escalation
from core.marketing.policy_manifest import evaluate_marketing_policy

CMO_REPORT_QUALITY_VERSION = "2026-05-23.cmo-7.3"

REPORT_GATE_STATUSES = ("pass", "warning", "blocked", "unavailable")
SAFE_REPORT_MODES = ("draft_only", "internal_only", "deliverable")

REPORT_TYPE_ALIASES = {
    "cmo_weekly": "weekly_marketing_report",
    "campaign_report": "campaign_performance_ad_hoc",
    "campaign_performance_report": "campaign_performance_ad_hoc",
    "board_summary": "executive_board_summary",
}

# enterprise-gate: process-local-ok reason=static-workflow-state-set
ACTIVE_WORKFLOW_STATES = {"active"}
# enterprise-gate: process-local-ok reason=static-workflow-state-set
INTERNAL_WORKFLOW_STATES = {"promotion_ready", "degraded"}
BLOCKING_WORKFLOW_STATES = {  # enterprise-gate: process-local-ok reason=static-workflow-state-set
    "promotion_blocked",
    "shadow",
    "paused",
    "unavailable",
}

BLOCKING_KPI_STATUSES = {"blocked", "unavailable"}
DEGRADED_KPI_STATUSES = {"degraded"}
BLOCKING_RECONCILIATION_STATUSES = {"blocked", "failed"}
WARNING_RECONCILIATION_STATUSES = {"warning", "unavailable"}
STALE_FRESHNESS_STATUSES = {"stale"}
UNKNOWN_FRESHNESS_STATUSES = {"unknown", "missing"}
DEMO_SOURCES = {
    "demo",
    "hardcoded",
    "sample",
    "mock",
    "report_generator_fallback",
    "fallback",
}


@dataclass(frozen=True)
class CmoReportQualitySpec:
    key: str
    display_name: str
    required_kpi_keys: tuple[str, ...]
    optional_kpi_keys: tuple[str, ...]
    critical_connector_categories: tuple[str, ...]
    workflow_key: str
    policy_action: str
    confidence_floor: float
    stale_required_sources_block: bool = True
    sensitive_by_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_key": self.key,
            "display_name": self.display_name,
            "required_kpi_keys": list(self.required_kpi_keys),
            "optional_kpi_keys": list(self.optional_kpi_keys),
            "critical_connector_categories": list(self.critical_connector_categories),
            "workflow_key": self.workflow_key,
            "policy_action": self.policy_action,
            "confidence_floor": self.confidence_floor,
            "stale_required_sources_block": self.stale_required_sources_block,
            "sensitive_by_default": self.sensitive_by_default,
        }


CMO_REPORT_QUALITY_SPECS: tuple[CmoReportQualitySpec, ...] = (
    CmoReportQualitySpec(
        key="weekly_marketing_report",
        display_name="Weekly Marketing Report",
        required_kpi_keys=(
            "cac",
            "mql",
            "sql",
            "mql_to_sql_conversion_rate",
            "roas",
            "pipeline_contribution",
            "conversion_rates_by_funnel_stage",
            "email_performance",
        ),
        optional_kpi_keys=("content_performance", "brand_sentiment"),
        critical_connector_categories=("CRM", "Ads", "Analytics", "Email"),
        workflow_key="weekly_marketing_report",
        policy_action="generate_weekly_report",
        confidence_floor=0.75,
    ),
    CmoReportQualitySpec(
        key="daily_ad_performance",
        display_name="Daily Ad Performance",
        required_kpi_keys=("cac", "roas", "mql", "conversion_rates_by_funnel_stage"),
        optional_kpi_keys=("experiment_velocity",),
        critical_connector_categories=("Ads", "CRM", "Analytics"),
        workflow_key="daily_spend_optimization",
        policy_action="generate_weekly_report",
        confidence_floor=0.80,
    ),
    CmoReportQualitySpec(
        key="monthly_marketing_roi",
        display_name="Monthly Marketing ROI",
        required_kpi_keys=("cac", "roas", "pipeline_contribution", "ltv_cac"),
        optional_kpi_keys=("content_performance", "email_performance", "brand_sentiment"),
        critical_connector_categories=("CRM", "Ads", "Analytics", "Finance"),
        workflow_key="weekly_marketing_report",
        policy_action="generate_weekly_report",
        confidence_floor=0.82,
    ),
    CmoReportQualitySpec(
        key="campaign_performance_ad_hoc",
        display_name="Campaign Performance Ad Hoc",
        required_kpi_keys=("roas", "mql", "sql", "conversion_rates_by_funnel_stage"),
        optional_kpi_keys=("content_performance", "email_performance"),
        critical_connector_categories=("CRM", "Ads", "Analytics"),
        workflow_key="weekly_marketing_report",
        policy_action="generate_weekly_report",
        confidence_floor=0.65,
        stale_required_sources_block=False,
    ),
    CmoReportQualitySpec(
        key="executive_board_summary",
        display_name="Executive Board Summary",
        required_kpi_keys=(
            "cac",
            "mql_to_sql_conversion_rate",
            "roas",
            "pipeline_contribution",
            "brand_sentiment",
        ),
        optional_kpi_keys=("ltv_cac", "content_performance", "email_performance"),
        critical_connector_categories=("CRM", "Ads", "Analytics", "Brand"),
        workflow_key="weekly_marketing_report",
        policy_action="generate_weekly_report",
        confidence_floor=0.85,
        sensitive_by_default=True,
    ),
)


def default_cmo_report_quality_specs() -> dict[str, Any]:
    return {
        "schema_version": CMO_REPORT_QUALITY_VERSION,
        "reports": [spec.to_dict() for spec in CMO_REPORT_QUALITY_SPECS],
    }


def build_cmo_report_quality_projection(
    *,
    kpi_results: Iterable[dict[str, Any]],
    reconciliation_checks: Iterable[dict[str, Any]],
    connector_setup: Iterable[dict[str, Any]] = (),
    data_readiness: Mapping[str, Any] | None = None,
    connector_contracts: Iterable[dict[str, Any]] = (),
    workflow_activation: Mapping[str, Any] | None = None,
    policy_projection: Mapping[str, Any] | None = None,
    escalation_projection: Mapping[str, Any] | None = None,
    decision_audit_projection: Mapping[str, Any] | None = None,
    source_context: Mapping[str, Any] | None = None,
    report_context: Mapping[str, Any] | None = None,
    report_types: Iterable[str] | None = None,
    production_tenant: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build report-level quality gates and a compact summary."""

    requested = {_normalize_key(report_type) for report_type in report_types or []}
    specs = [
        spec
        for spec in CMO_REPORT_QUALITY_SPECS
        if not requested or spec.key in {_canonical_report_key(item) for item in requested}
    ]
    gates = [
        evaluate_cmo_report_quality_gate(
            spec.key,
            kpi_results=kpi_results,
            reconciliation_checks=reconciliation_checks,
            connector_setup=connector_setup,
            data_readiness=data_readiness,
            connector_contracts=connector_contracts,
            workflow_activation=workflow_activation,
            policy_projection=policy_projection,
            escalation_projection=escalation_projection,
            decision_audit_projection=decision_audit_projection,
            source_context=source_context,
            report_context=_context_for_report(report_context, spec.key),
            production_tenant=production_tenant,
            now=now,
        )
        for spec in specs
    ]
    return {
        "cmo_report_quality_version": CMO_REPORT_QUALITY_VERSION,
        "report_quality_spec": default_cmo_report_quality_specs(),
        "report_quality_gates": gates,
        "report_quality_summary": summarize_cmo_report_quality_gates(gates),
    }


def build_cmo_report_quality_projection_from_kpi_payload(
    payload: Mapping[str, Any] | None,
    *,
    report_context: Mapping[str, Any] | None = None,
    report_types: Iterable[str] | None = None,
    production_tenant: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate report quality using the `/kpis/cmo` response shape."""

    data = payload if isinstance(payload, Mapping) else {}
    workflow_activation = {
        "workflow_activation_status": _list_from_value(data.get("workflow_activation_status")),
        "workflow_activation_summary": data.get("workflow_activation_summary") or {},
    }
    policy_projection = {
        "marketing_policy_manifest": data.get("marketing_policy_manifest"),
        "marketing_policy_summary": data.get("marketing_policy_summary") or {},
    }
    escalation_projection = {
        "marketing_escalation_matrix": data.get("marketing_escalation_matrix"),
        "marketing_escalation_summary": data.get("marketing_escalation_summary") or {},
    }
    decision_audit_projection = {
        "marketing_decision_audit": data.get("marketing_decision_audit"),
        "marketing_decision_audit_summary": data.get("marketing_decision_audit_summary") or {},
    }
    return build_cmo_report_quality_projection(
        kpi_results=_list_from_value(data.get("unified_cmo_kpi_results")),
        reconciliation_checks=_list_from_value(data.get("cmo_kpi_reconciliation_checks")),
        connector_setup=_list_from_value(data.get("connector_setup")),
        data_readiness={
            "field_mapping_status": _list_from_value(data.get("field_mapping_status")),
            "backfill_status": _list_from_value(data.get("backfill_status")),
            "kpi_readiness": data.get("kpi_readiness") or {},
        },
        connector_contracts=_list_from_value(data.get("connector_contracts")),
        workflow_activation=workflow_activation,
        policy_projection=policy_projection,
        escalation_projection=escalation_projection,
        decision_audit_projection=decision_audit_projection,
        source_context=data,
        report_context=report_context,
        report_types=report_types,
        production_tenant=production_tenant,
        now=now,
    )


def evaluate_cmo_report_quality_gate(
    report_key: str,
    *,
    kpi_results: Iterable[dict[str, Any]],
    reconciliation_checks: Iterable[dict[str, Any]],
    connector_setup: Iterable[dict[str, Any]] = (),
    data_readiness: Mapping[str, Any] | None = None,
    connector_contracts: Iterable[dict[str, Any]] = (),
    workflow_activation: Mapping[str, Any] | None = None,
    policy_projection: Mapping[str, Any] | None = None,
    escalation_projection: Mapping[str, Any] | None = None,
    decision_audit_projection: Mapping[str, Any] | None = None,
    source_context: Mapping[str, Any] | None = None,
    report_context: Mapping[str, Any] | None = None,
    production_tenant: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate one CMO report quality gate."""

    spec = _spec_for_report(report_key)
    timestamp = _ensure_aware(now) or datetime.now(UTC)
    if spec is None:
        return _unavailable_gate(report_key, timestamp)

    kpis = _kpis_by_key(kpi_results)
    checks = [dict(item) for item in reconciliation_checks if isinstance(item, Mapping)]
    setup_rows = [dict(item) for item in connector_setup if isinstance(item, Mapping)]
    contract_rows = [dict(item) for item in connector_contracts if isinstance(item, Mapping)]
    workflow_rows = _workflow_rows(workflow_activation)
    source = source_context if isinstance(source_context, Mapping) else {}
    context = report_context if isinstance(report_context, Mapping) else {}

    blockers: list[str] = []
    warnings: list[str] = []
    missing_requirements: dict[str, list[str]] = {
        "kpis": [],
        "connectors": [],
        "field_mappings": [],
        "backfills": [],
        "reconciliation": [],
        "workflow": [],
        "policy": [],
        "approval": [],
        "escalation": [],
        "audit": [],
        "source_data": [],
    }
    stale_missing_source_refs: list[dict[str, Any]] = []
    failed_reconciliation_keys: list[str] = []
    required_approval_refs: list[str] = []
    required_escalation_refs: list[str] = []
    required_audit_refs: list[str] = []

    required_results = [kpis.get(key) for key in spec.required_kpi_keys]
    optional_results = [kpis.get(key) for key in spec.optional_kpi_keys]
    blocked_kpis: list[str] = []
    degraded_kpis: list[str] = []
    confidences: list[float] = []

    for key, result in zip(spec.required_kpi_keys, required_results, strict=False):
        if not result:
            blocked_kpis.append(key)
            missing_requirements["kpis"].append(key)
            blockers.append(f"Required KPI {key} is missing from the unified KPI projection.")
            confidences.append(0.0)
            continue
        status = str(result.get("status") or "unavailable")
        confidences.append(float(result.get("confidence") or 0.0))
        _collect_missing_from_kpi(result, missing_requirements)
        if status in BLOCKING_KPI_STATUSES:
            blocked_kpis.append(key)
            blockers.append(f"Required KPI {key} is {status}.")
        elif status in DEGRADED_KPI_STATUSES:
            degraded_kpis.append(key)
            warnings.append(f"Required KPI {key} is degraded.")
        freshness_status = str(result.get("freshness_status") or "")
        if freshness_status in STALE_FRESHNESS_STATUSES | UNKNOWN_FRESHNESS_STATUSES:
            stale_missing_source_refs.extend(_kpi_source_refs(result, key, freshness_status))
            if freshness_status in STALE_FRESHNESS_STATUSES and spec.stale_required_sources_block:
                blocked_kpis.append(key)
                blockers.append(f"Required KPI {key} has stale source data.")
            else:
                degraded_kpis.append(key)
                warnings.append(f"Required KPI {key} freshness is {freshness_status}.")

    for key, result in zip(spec.optional_kpi_keys, optional_results, strict=False):
        if not result:
            continue
        status = str(result.get("status") or "")
        freshness_status = str(result.get("freshness_status") or "")
        if status in BLOCKING_KPI_STATUSES | DEGRADED_KPI_STATUSES:
            degraded_kpis.append(key)
            warnings.append(f"Optional KPI {key} is {status}.")
        if freshness_status in STALE_FRESHNESS_STATUSES | UNKNOWN_FRESHNESS_STATUSES:
            stale_missing_source_refs.extend(_kpi_source_refs(result, key, freshness_status))
            degraded_kpis.append(key)
            warnings.append(f"Optional KPI {key} freshness is {freshness_status}.")

    actual_confidence = round(min(confidences), 3) if confidences else 0.0
    if actual_confidence < spec.confidence_floor:
        blockers.append(
            f"Required KPI confidence {actual_confidence:.3f} is below floor {spec.confidence_floor:.3f}."
        )
        missing_requirements["source_data"].append("confidence_floor")

    reconciliation = _evaluate_reconciliation(spec, checks)
    blockers.extend(reconciliation["blockers"])
    warnings.extend(reconciliation["warnings"])
    failed_reconciliation_keys.extend(reconciliation["failed_keys"])
    missing_requirements["reconciliation"].extend(reconciliation["missing"])
    stale_missing_source_refs.extend(reconciliation["source_refs"])

    connector_quality = _evaluate_connector_quality(spec, setup_rows, contract_rows)
    blockers.extend(connector_quality["blockers"])
    warnings.extend(connector_quality["warnings"])
    stale_missing_source_refs.extend(connector_quality["source_refs"])
    missing_requirements["connectors"].extend(connector_quality["missing"])

    readiness_quality = _evaluate_data_readiness_quality(spec, data_readiness, spec.required_kpi_keys)
    blockers.extend(readiness_quality["blockers"])
    warnings.extend(readiness_quality["warnings"])
    missing_requirements["field_mappings"].extend(readiness_quality["field_mappings"])
    missing_requirements["backfills"].extend(readiness_quality["backfills"])

    workflow_quality = _evaluate_workflow_quality(spec, workflow_rows)
    blockers.extend(workflow_quality["blockers"])
    warnings.extend(workflow_quality["warnings"])
    missing_requirements["workflow"].extend(workflow_quality["missing"])

    governance = _evaluate_governance_quality(
        spec,
        policy_projection,
        escalation_projection,
        decision_audit_projection,
        context,
        timestamp,
    )
    blockers.extend(governance["blockers"])
    warnings.extend(governance["warnings"])
    required_approval_refs.extend(governance["approval_refs"])
    required_escalation_refs.extend(governance["escalation_refs"])
    required_audit_refs.extend(governance["audit_refs"])
    for key, values in governance["missing"].items():
        missing_requirements[key].extend(values)

    source_quality = _evaluate_source_context(source, production_tenant)
    blockers.extend(source_quality["blockers"])
    missing_requirements["source_data"].extend(source_quality["missing"])

    blocked_kpis = _unique(blocked_kpis)
    degraded_kpis = [key for key in _unique(degraded_kpis) if key not in set(blocked_kpis)]
    blockers = _unique(blockers)
    warnings = _unique(warnings)
    failed_reconciliation_keys = _unique(failed_reconciliation_keys)
    stale_missing_source_refs = _unique_refs(stale_missing_source_refs)
    missing_requirements = {
        key: _unique(values)
        for key, values in missing_requirements.items()
        if _unique(values)
    }

    if blockers:
        status = "blocked"
        safe_mode = "draft_only"
        severity = "critical" if source_quality["blockers"] else "high"
    elif warnings:
        status = "warning"
        safe_mode = "internal_only"
        severity = "medium"
    else:
        status = "pass"
        safe_mode = "deliverable"
        severity = "low"

    next_action = _next_action(
        missing_requirements,
        failed_reconciliation_keys,
        stale_missing_source_refs,
        warnings,
    )
    gate = {
        "report_key": spec.key,
        "report_type": spec.key,
        "display_name": spec.display_name,
        "status": status,
        "severity": severity,
        "required_kpi_keys": list(spec.required_kpi_keys),
        "optional_kpi_keys": list(spec.optional_kpi_keys),
        "blocked_kpi_keys": blocked_kpis,
        "degraded_kpi_keys": degraded_kpis,
        "failed_reconciliation_keys": failed_reconciliation_keys,
        "stale_missing_source_refs": stale_missing_source_refs,
        "confidence_floor": spec.confidence_floor,
        "actual_confidence": actual_confidence,
        "required_approval_refs": _unique(required_approval_refs),
        "required_escalation_refs": _unique(required_escalation_refs),
        "required_audit_refs": _unique(required_audit_refs),
        "missing_requirements": missing_requirements,
        "blocked_reasons": blockers,
        "warning_reasons": warnings,
        "next_action_cta": next_action,
        "safe_report_mode": safe_mode,
        "trusted_delivery_allowed": safe_mode == "deliverable",
        "evaluated_at": timestamp.isoformat(),
    }
    audit_package = build_cmo_decision_audit_package(
        {
            "event_type": "policy_decision",
            "decision_type": "report_quality_gate",
            "workflow_id": spec.workflow_key,
            "action": spec.key,
            "capability": "cmo_report_quality",
            "source_refs": stale_missing_source_refs,
            "confidence": actual_confidence,
            "status": status,
            "reason": "; ".join(blockers or warnings or ["Report quality gate passed."]),
            "final_outcome": safe_mode,
            "input_snapshot": {
                "required_kpi_keys": list(spec.required_kpi_keys),
                "blocked_kpi_keys": blocked_kpis,
                "failed_reconciliation_keys": failed_reconciliation_keys,
                "missing_requirements": missing_requirements,
            },
        },
        now=timestamp,
    )
    gate["decision_audit_ref"] = audit_package["audit_reference"]
    return gate


def summarize_cmo_report_quality_gates(gates: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(gates)
    counts = dict.fromkeys(REPORT_GATE_STATUSES, 0)
    modes = dict.fromkeys(SAFE_REPORT_MODES, 0)
    for item in items:
        status = str(item.get("status") or "")
        mode = str(item.get("safe_report_mode") or "")
        if status in counts:
            counts[status] += 1
        if mode in modes:
            modes[mode] += 1

    readiness = (
        "blocked"
        if counts["blocked"] or counts["unavailable"]
        else "degraded"
        if counts["warning"]
        else "ready"
    )
    return {
        "schema_version": CMO_REPORT_QUALITY_VERSION,
        "total": len(items),
        **counts,
        **{f"{key}_reports": value for key, value in modes.items()},
        "readiness": readiness,
        "deliverable_report_keys": [
            str(item.get("report_key"))
            for item in items
            if item.get("safe_report_mode") == "deliverable"
        ],
        "blocked_report_keys": [
            str(item.get("report_key"))
            for item in items
            if item.get("status") in {"blocked", "unavailable"}
        ],
        "needs_action": sum(1 for item in items if item.get("next_action_cta") != "none"),
        "next_action_cta": next(
            (
                str(item.get("next_action_cta"))
                for item in items
                if item.get("next_action_cta") not in {None, "", "none"}
            ),
            "none",
        ),
    }


def cmo_report_gate_for_type(
    report_type: str,
    payload: Mapping[str, Any] | None,
    *,
    production_tenant: bool = False,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return a gate for a CMO report type from a KPI payload."""

    key = _canonical_report_key(report_type)
    if _spec_for_report(key) is None:
        return None
    data = payload if isinstance(payload, Mapping) else {}
    for gate in data.get("report_quality_gates") or []:
        if isinstance(gate, Mapping) and _canonical_report_key(gate.get("report_key")) == key:
            return dict(gate)
    projection = build_cmo_report_quality_projection_from_kpi_payload(
        data,
        report_types=(key,),
        production_tenant=production_tenant,
        now=now,
    )
    gates = projection["report_quality_gates"]
    return dict(gates[0]) if gates else None


def cmo_report_trusted_delivery_allowed(gate: Mapping[str, Any] | None) -> bool:
    """Return true only when a report gate permits trusted external delivery."""

    if not isinstance(gate, Mapping):
        return False
    return str(gate.get("safe_report_mode") or "") == "deliverable" and str(gate.get("status") or "") == "pass"


def _evaluate_reconciliation(
    spec: CmoReportQualitySpec,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    required = set(spec.required_kpi_keys)
    optional = set(spec.optional_kpi_keys)
    blockers: list[str] = []
    warnings: list[str] = []
    failed_keys: list[str] = []
    missing: list[str] = []
    source_refs: list[dict[str, Any]] = []

    for check in checks:
        affected = {_normalize_key(key) for key in check.get("affected_kpi_keys") or []}
        if not affected & (required | optional):
            continue
        status = str(check.get("status") or "unavailable")
        check_key = str(check.get("reconciliation_key") or "unknown_reconciliation")
        affects_required = bool(affected & required)
        if status in BLOCKING_RECONCILIATION_STATUSES:
            failed_keys.append(check_key)
            source_refs.extend(_refs_from_check(check, check_key))
            if affects_required or check.get("blocks_kpi_readiness"):
                blockers.append(f"Reconciliation {check_key} is {status}.")
            else:
                warnings.append(f"Optional reconciliation {check_key} is {status}.")
        elif status in WARNING_RECONCILIATION_STATUSES:
            warnings.append(f"Reconciliation {check_key} is {status}.")
            source_refs.extend(_refs_from_check(check, check_key))
        for values in (check.get("missing_requirements") or {}).values():
            for value in values:
                missing.append(f"{check_key}:{value}")

    return {
        "blockers": blockers,
        "warnings": warnings,
        "failed_keys": failed_keys,
        "missing": missing,
        "source_refs": source_refs,
    }


def _evaluate_connector_quality(
    spec: CmoReportQualitySpec,
    setup_rows: list[dict[str, Any]],
    contract_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []
    source_refs: list[dict[str, Any]] = []

    for category in spec.critical_connector_categories:
        setup = _rows_for_category(setup_rows, category)
        contracts = _rows_for_category(contract_rows, category)
        if not setup:
            missing.append(category)
            source_refs.append({"type": "connector_category", "category": category, "status": "missing"})
            blockers.append(f"Critical {category} connector setup is missing.")
        elif not any(_connector_configured(row) for row in setup):
            missing.append(category)
            source_refs.extend(_setup_refs(setup, category))
            blockers.append(f"Critical {category} connector is not configured.")
        else:
            health_states = {_health(row) for row in setup}
            if health_states & {"missing", "unconfigured", "auth_expired", "insufficient_scope", "unhealthy"}:
                source_refs.extend(_setup_refs(setup, category))
                blockers.append(f"Critical {category} connector health is {', '.join(sorted(health_states))}.")
            elif health_states & {"stale", "degraded", "partial"}:
                source_refs.extend(_setup_refs(setup, category))
                if spec.stale_required_sources_block:
                    blockers.append(f"Critical {category} connector health is {', '.join(sorted(health_states))}.")
                else:
                    warnings.append(f"Critical {category} connector health is {', '.join(sorted(health_states))}.")

        if not contracts:
            missing.append(f"{category}:contract")
            source_refs.append({"type": "connector_contract", "category": category, "status": "missing"})
            blockers.append(f"Critical {category} connector contract is missing.")
            continue
        if any(row.get("blocks_production_kpi_confidence") for row in contracts):
            source_refs.extend(_contract_refs(contracts, category))
            blockers.append(f"Critical {category} connector contract blocks production KPI confidence.")
        elif any(not row.get("read_ready", True) for row in contracts):
            source_refs.extend(_contract_refs(contracts, category))
            blockers.append(f"Critical {category} connector contract is not read-ready.")
        elif any(_contract_degraded(row) for row in contracts):
            source_refs.extend(_contract_refs(contracts, category))
            warnings.append(f"Critical {category} connector contract is degraded.")

    return {
        "blockers": blockers,
        "warnings": warnings,
        "missing": missing,
        "source_refs": source_refs,
    }


def _evaluate_data_readiness_quality(
    spec: CmoReportQualitySpec,
    data_readiness: Mapping[str, Any] | None,
    required_kpi_keys: tuple[str, ...],
) -> dict[str, Any]:
    readiness = data_readiness if isinstance(data_readiness, Mapping) else {}
    blockers: list[str] = []
    warnings: list[str] = []
    missing_mappings: list[str] = []
    missing_backfills: list[str] = []

    kpi_readiness = readiness.get("kpi_readiness") if isinstance(readiness.get("kpi_readiness"), Mapping) else {}
    for key in required_kpi_keys:
        row = kpi_readiness.get(key) if isinstance(kpi_readiness, Mapping) else None
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or row.get("readiness") or "")
        if status in {"blocked", "unavailable"}:
            blockers.append(f"KPI readiness for {key} is {status}.")
        elif status in {"degraded", "warning"}:
            warnings.append(f"KPI readiness for {key} is {status}.")

    for row in readiness.get("field_mapping_status") or []:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "")
        if status in {"unmapped", "invalid", "blocked"}:
            missing_mappings.append(str(row.get("key") or "unknown_mapping"))
    for row in readiness.get("backfill_status") or []:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "")
        category = str(row.get("category") or row.get("source_connector_key") or "unknown_source")
        if status in {"failed", "blocked"}:
            missing_backfills.append(category)

    if missing_mappings:
        blockers.append("One or more field mappings are invalid, blocked, or unmapped.")
    if missing_backfills:
        blockers.append("One or more source backfills are failed or blocked.")

    return {
        "blockers": blockers,
        "warnings": warnings,
        "field_mappings": missing_mappings,
        "backfills": missing_backfills,
    }


def _evaluate_workflow_quality(
    spec: CmoReportQualitySpec,
    workflow_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    row = _workflow_row(workflow_rows, spec.workflow_key)
    if row is None:
        return {
            "blockers": [f"Workflow activation state for {spec.workflow_key} is missing."],
            "warnings": [],
            "missing": [spec.workflow_key],
        }
    state = str(row.get("state") or row.get("mode") or "unavailable")
    if state in ACTIVE_WORKFLOW_STATES:
        return {"blockers": [], "warnings": [], "missing": []}
    if state in INTERNAL_WORKFLOW_STATES:
        return {
            "blockers": [],
            "warnings": [f"Workflow {spec.workflow_key} is {state}; report may be internal only."],
            "missing": [],
        }
    if state in BLOCKING_WORKFLOW_STATES:
        return {
            "blockers": [f"Workflow {spec.workflow_key} is {state}."],
            "warnings": [],
            "missing": [spec.workflow_key],
        }
    return {
        "blockers": [f"Workflow {spec.workflow_key} state {state} is not deliverable."],
        "warnings": [],
        "missing": [spec.workflow_key],
    }


def _evaluate_governance_quality(
    spec: CmoReportQualitySpec,
    policy_projection: Mapping[str, Any] | None,
    escalation_projection: Mapping[str, Any] | None,
    decision_audit_projection: Mapping[str, Any] | None,
    context: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    missing: dict[str, list[str]] = {"policy": [], "approval": [], "escalation": [], "audit": []}
    approval_refs = _string_list(
        context.get("approval_refs")
        or context.get("approval_ref")
        or context.get("delivery_approval_ref")
    )
    escalation_refs = _string_list(
        context.get("escalation_refs")
        or context.get("escalation_ref")
        or context.get("delivery_escalation_ref")
    )
    audit_refs = _string_list(
        context.get("audit_refs")
        or context.get("decision_audit_refs")
        or context.get("delivery_audit_ref")
    )
    external_delivery = _truthy(context.get("external_delivery_requested") or context.get("deliver_externally"))
    sensitive = bool(spec.sensitive_by_default) or _truthy(
        context.get("contains_sensitive_claims")
        or context.get("sensitive_claims")
        or context.get("pricing_claim")
        or context.get("legal_claim")
        or context.get("high_risk_copy")
    )

    policy_missing = _projection_missing(policy_projection, "marketing_policy_summary", "missing_policy")
    audit_missing = _projection_missing(
        decision_audit_projection,
        "marketing_decision_audit_summary",
        "missing_audit_evidence",
    )
    escalation_missing = _projection_missing(
        escalation_projection,
        "marketing_escalation_summary",
        "missing_escalation_route",
    )

    if policy_missing:
        blockers.append("Marketing policy manifest is missing for report delivery.")
        missing["policy"].append("marketing_policy_manifest")
        policy_decision = None
    else:
        manifest = None
        if isinstance(policy_projection, Mapping):
            manifest = policy_projection.get("marketing_policy_manifest")
        policy_decision = evaluate_marketing_policy(
            {
                "workflow_id": spec.workflow_key,
                "workflow_mode": "active",
                "action": spec.policy_action,
                "customer_facing": external_delivery,
                "external_write_required": external_delivery,
                "pricing_claim": _truthy(context.get("pricing_claim")),
                "legal_claim": _truthy(context.get("legal_claim")),
                "high_risk_copy": _truthy(context.get("high_risk_copy")),
                "crisis_response": _truthy(context.get("crisis_response")),
                "public_response": _truthy(context.get("public_response")),
            },
            manifest=manifest if isinstance(manifest, Mapping) else None,
            use_default=True,
        )
        decision = str(policy_decision.get("decision") or "")
        if decision in {"blocked", "missing_policy", "read_only_only"}:
            blockers.append(f"Marketing policy decision for report delivery is {decision}.")
            missing["policy"].append(spec.policy_action)
        elif decision in {"requires_approval", "requires_escalation"}:
            if external_delivery or sensitive:
                if not approval_refs:
                    blockers.append("Report delivery requires approval evidence.")
                    missing["approval"].append("delivery_approval_ref")
                if decision == "requires_escalation" and not escalation_refs:
                    blockers.append("Report delivery requires escalation evidence.")
                    missing["escalation"].append("delivery_escalation_ref")
            else:
                warnings.append(f"Policy decision for report is {decision}.")
        if policy_decision.get("decision_audit_ref"):
            audit_refs.append(str(policy_decision["decision_audit_ref"]))

    if external_delivery or sensitive:
        if audit_missing:
            blockers.append("Decision audit package readiness is missing for report delivery.")
            missing["audit"].append("marketing_decision_audit")
        if escalation_missing and sensitive:
            blockers.append("Escalation route readiness is missing for sensitive report delivery.")
            missing["escalation"].append("marketing_escalation_matrix")

    if sensitive and not escalation_refs:
        escalation = evaluate_marketing_escalation(
            {
                "trigger_type": "pricing_or_legal_claim"
                if _truthy(context.get("pricing_claim") or context.get("legal_claim"))
                else "high_risk_copy",
                "workflow_id": spec.workflow_key,
                "action": spec.policy_action,
                "severity": "high",
            },
            now=now,
        )
        if escalation.get("audit_reference"):
            escalation_refs.append(str(escalation["audit_reference"]))

    return {
        "blockers": blockers,
        "warnings": warnings,
        "approval_refs": approval_refs,
        "escalation_refs": escalation_refs,
        "audit_refs": audit_refs,
        "missing": missing,
    }


def _evaluate_source_context(
    source: Mapping[str, Any],
    production_tenant: bool,
) -> dict[str, Any]:
    blockers: list[str] = []
    missing: list[str] = []
    demo = _truthy(source.get("demo"))
    production_blocked = _truthy(source.get("production_data_blocked"))
    source_label = _normalize_key(source.get("source"))
    if production_blocked:
        blockers.append("Production CMO data policy blocked real-data readiness.")
        missing.append("production_data_blocked")
    if production_tenant and (demo or source_label in DEMO_SOURCES):
        blockers.append("Production report cannot use demo, sample, hardcoded, mock, or fallback KPI data.")
        missing.append("real_tenant_kpi_data")
    return {"blockers": blockers, "missing": missing}


def _unavailable_gate(report_key: str, timestamp: datetime) -> dict[str, Any]:
    key = _canonical_report_key(report_key)
    return {
        "report_key": key,
        "report_type": key,
        "display_name": key.replace("_", " ").title(),
        "status": "unavailable",
        "severity": "high",
        "required_kpi_keys": [],
        "blocked_kpi_keys": [],
        "degraded_kpi_keys": [],
        "failed_reconciliation_keys": [],
        "stale_missing_source_refs": [],
        "confidence_floor": None,
        "actual_confidence": 0.0,
        "required_approval_refs": [],
        "required_escalation_refs": [],
        "required_audit_refs": [],
        "missing_requirements": {"report": [key]},
        "blocked_reasons": ["Report type is not covered by CMO report quality gates."],
        "warning_reasons": [],
        "next_action_cta": "add_report_quality_spec",
        "safe_report_mode": "draft_only",
        "trusted_delivery_allowed": False,
        "evaluated_at": timestamp.isoformat(),
    }


def _next_action(
    missing_requirements: Mapping[str, list[str]],
    failed_reconciliation_keys: list[str],
    stale_missing_source_refs: list[dict[str, Any]],
    warnings: list[str],
) -> str:
    if missing_requirements.get("connectors"):
        return "fix_report_connectors"
    if missing_requirements.get("field_mappings"):
        return "fix_required_mapping"
    if missing_requirements.get("backfills"):
        return "complete_backfill"
    if missing_requirements.get("kpis"):
        return "restore_required_kpis"
    if failed_reconciliation_keys or missing_requirements.get("reconciliation"):
        return "resolve_report_reconciliation"
    if stale_missing_source_refs:
        return "refresh_report_sources"
    if missing_requirements.get("source_data"):
        return "connect_real_kpi_sources"
    if missing_requirements.get("workflow"):
        return "activate_report_workflow"
    if missing_requirements.get("policy"):
        return "configure_marketing_policy_manifest"
    if missing_requirements.get("approval"):
        return "capture_report_delivery_approval"
    if missing_requirements.get("escalation"):
        return "configure_report_escalation_route"
    if missing_requirements.get("audit"):
        return "configure_decision_audit_package"
    if warnings:
        return "review_report_quality_warnings"
    return "none"


def _collect_missing_from_kpi(result: Mapping[str, Any], missing: dict[str, list[str]]) -> None:
    requirements = result.get("missing_requirements") if isinstance(result.get("missing_requirements"), Mapping) else {}
    for key, target in (
        ("connectors", "connectors"),
        ("field_mappings", "field_mappings"),
        ("backfills", "backfills"),
        ("reconciliation", "reconciliation"),
        ("fields", "source_data"),
    ):
        for value in requirements.get(key) or []:
            missing[target].append(str(value))


def _evaluate_report_gate_from_payload(
    report_type: str,
    payload: Mapping[str, Any],
    *,
    production_tenant: bool = False,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    return cmo_report_gate_for_type(
        report_type,
        payload,
        production_tenant=production_tenant,
        now=now,
    )


def _spec_for_report(report_key: Any) -> CmoReportQualitySpec | None:
    key = _canonical_report_key(report_key)
    for spec in CMO_REPORT_QUALITY_SPECS:
        if spec.key == key:
            return spec
    return None


def _canonical_report_key(value: Any) -> str:
    key = _normalize_key(value)
    return REPORT_TYPE_ALIASES.get(key, key)


def _kpis_by_key(kpi_results: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        _normalize_key(row.get("kpi_key")): dict(row)
        for row in kpi_results
        if isinstance(row, Mapping) and row.get("kpi_key")
    }


def _workflow_rows(workflow_activation: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(workflow_activation, Mapping):
        return []
    return [
        dict(row)
        for row in workflow_activation.get("workflow_activation_status") or []
        if isinstance(row, Mapping)
    ]


def _workflow_row(rows: list[dict[str, Any]], workflow_key: str) -> dict[str, Any] | None:
    key = _normalize_key(workflow_key)
    for row in rows:
        if _normalize_key(row.get("workflow_key") or row.get("key")) == key:
            return row
    return None


def _rows_for_category(rows: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    target = _normalize_key(category)
    return [
        row
        for row in rows
        if _normalize_key(row.get("category") or row.get("connector_category")) == target
    ]


def _connector_configured(row: Mapping[str, Any]) -> bool:
    status = _normalize_key(row.get("status") or row.get("configured_status") or row.get("setup_status"))
    if status in {"unconfigured", "missing", "not_configured"}:
        return False
    if row.get("configured") is False:
        return False
    return True


def _health(row: Mapping[str, Any]) -> str:
    return _normalize_key(
        row.get("health_status")
        or row.get("health")
        or row.get("status")
        or row.get("read_status")
        or "missing"
    )


def _contract_degraded(row: Mapping[str, Any]) -> bool:
    state = _normalize_key(
        row.get("read_status")
        or row.get("health_status")
        or row.get("contract_state")
        or row.get("status")
    )
    failure_class = _normalize_key(row.get("failure_class"))
    if state in {"degraded", "partial_data", "stale_data", "rate_limited", "timeout", "vendor_5xx"}:
        return True
    if failure_class in {"partial_data", "stale_data", "rate_limited", "timeout", "vendor_5xx"}:
        return True
    degraded_mode = row.get("degraded_mode")
    return isinstance(degraded_mode, Mapping) and bool(degraded_mode.get("allowed"))


def _setup_refs(rows: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "connector_setup",
            "category": category,
            "connector_key": row.get("connector_key") or row.get("key"),
            "status": row.get("status") or row.get("configured_status"),
            "health_status": row.get("health_status") or row.get("health"),
            "last_sync_at": row.get("last_sync_at"),
        }
        for row in rows
    ]


def _contract_refs(rows: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "connector_contract",
            "category": category,
            "connector_key": row.get("connector_key") or row.get("key"),
            "read_status": row.get("read_status"),
            "health_status": row.get("health_status"),
            "failure_class": row.get("failure_class"),
        }
        for row in rows
    ]


def _refs_from_check(check: Mapping[str, Any], check_key: str) -> list[dict[str, Any]]:
    refs = []
    for ref in check.get("source_refs") or []:
        if isinstance(ref, Mapping):
            refs.append({"type": "reconciliation", "reconciliation_key": check_key, **dict(ref)})
        else:
            refs.append({"type": "reconciliation", "reconciliation_key": check_key, "ref": str(ref)})
    if not refs:
        refs.append({"type": "reconciliation", "reconciliation_key": check_key})
    return refs


def _kpi_source_refs(result: Mapping[str, Any], key: str, freshness_status: str) -> list[dict[str, Any]]:
    refs = []
    for ref in result.get("source_refs") or []:
        if isinstance(ref, Mapping):
            refs.append({"type": "kpi_source", "kpi_key": key, "freshness_status": freshness_status, **dict(ref)})
        else:
            refs.append({"type": "kpi_source", "kpi_key": key, "freshness_status": freshness_status, "ref": str(ref)})
    if not refs:
        refs.append({"type": "kpi_source", "kpi_key": key, "freshness_status": freshness_status})
    return refs


def _projection_missing(
    projection: Mapping[str, Any] | None,
    summary_key: str,
    missing_status: str,
) -> bool:
    if not isinstance(projection, Mapping):
        return False
    summary = projection.get(summary_key)
    if not isinstance(summary, Mapping):
        return False
    return _normalize_key(summary.get("status") or summary.get("readiness")) == _normalize_key(missing_status)


def _context_for_report(
    report_context: Mapping[str, Any] | None,
    report_key: str,
) -> dict[str, Any]:
    if not isinstance(report_context, Mapping):
        return {}
    base = dict(report_context.get("*") or {}) if isinstance(report_context.get("*"), Mapping) else {}
    specific = report_context.get(report_key)
    if isinstance(specific, Mapping):
        base.update(specific)
        return base
    if not base:
        return dict(report_context)
    return base


def _list_from_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    result = []
    for item in _list_from_value(value):
        text = str(item).strip()
        if text:
            result.append(text)
    return _unique(result)


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
    result: list[dict[str, Any]] = []
    for value in values:
        key = repr(sorted(value.items()))
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
