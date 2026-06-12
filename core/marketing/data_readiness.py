"""CMO marketing field mapping and historical backfill readiness.

This module projects stored ConnectorConfig metadata into operator-facing
CMO data readiness. It does not call vendor APIs or infer production
readiness from connector registration. Field mappings and backfill facts must
be explicitly present in connector config JSON before KPI paths can treat them
as ready.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.marketing.connector_retry_policy import summarize_degraded_modes
from core.marketing.decision_audit import build_cmo_decision_audit_package
from core.marketing.escalation_matrix import evaluate_marketing_escalation

MAPPING_STALE_AFTER = timedelta(days=90)

FIELD_MAPPING_STATES = (
    "unmapped",
    "partially_mapped",
    "valid",
    "invalid",
    "stale",
    "blocked",
)
BACKFILL_STATES = (
    "not_started",
    "queued",
    "running",
    "completed",
    "partial",
    "failed",
    "blocked",
)
ACTIVE_CONNECTOR_HEALTH = {"healthy", "stale", "degraded"}
BLOCKED_CONNECTOR_HEALTH = {"missing", "expired_auth", "insufficient_scope"}
VALID_CURRENCIES = {
    "AED",
    "AUD",
    "CAD",
    "EUR",
    "GBP",
    "INR",
    "JPY",
    "SGD",
    "USD",
}


@dataclass(frozen=True)
class FieldMappingRequirement:
    key: str
    name: str
    source_categories: tuple[str, ...]
    required_fields: tuple[str, ...]
    affected_kpis: tuple[str, ...]
    missing_blocks: bool = True


FIELD_MAPPING_REQUIREMENTS: tuple[FieldMappingRequirement, ...] = (
    FieldMappingRequirement(
        key="lifecycle_stages",
        name="Lifecycle stages",
        source_categories=("CRM",),
        required_fields=("source_field", "stage_map"),
        affected_kpis=("MQL", "SQL", "Pipeline contribution", "Conversion rates"),
    ),
    FieldMappingRequirement(
        key="opportunity_revenue",
        name="Opportunity revenue fields",
        source_categories=("CRM", "Finance"),
        required_fields=("amount_field", "close_date_field", "currency_field"),
        affected_kpis=("Pipeline contribution", "CAC", "ROAS"),
    ),
    FieldMappingRequirement(
        key="campaign_ids",
        name="Campaign IDs",
        source_categories=("Ads", "Analytics", "Email", "CMS"),
        required_fields=("campaign_id_field",),
        affected_kpis=("ROAS", "Attribution", "Experiment velocity"),
        missing_blocks=False,
    ),
    FieldMappingRequirement(
        key="utm_fields",
        name="UTM fields",
        source_categories=("Ads", "Analytics", "Email", "CMS"),
        required_fields=("source", "medium", "campaign"),
        affected_kpis=("ROAS", "CAC", "Attribution"),
        missing_blocks=False,
    ),
    FieldMappingRequirement(
        key="account_domains",
        name="Account domains",
        source_categories=("CRM", "ABM"),
        required_fields=("domain_field",),
        affected_kpis=("ABM readiness", "Pipeline contribution"),
        missing_blocks=False,
    ),
    FieldMappingRequirement(
        key="consent_unsubscribe",
        name="Consent and unsubscribe fields",
        source_categories=("Email", "CRM"),
        required_fields=("consent_field", "unsubscribe_field"),
        affected_kpis=("Email performance", "Lead nurture readiness"),
    ),
    FieldMappingRequirement(
        key="fiscal_calendar",
        name="Fiscal calendar",
        source_categories=(),
        required_fields=("fiscal_year_start_month",),
        affected_kpis=("CAC", "ROAS", "Pipeline contribution"),
    ),
    FieldMappingRequirement(
        key="currency",
        name="Currency",
        source_categories=(),
        required_fields=("currency",),
        affected_kpis=("CAC", "ROAS", "Pipeline contribution"),
    ),
    FieldMappingRequirement(
        key="timezone",
        name="Timezone",
        source_categories=(),
        required_fields=("timezone",),
        affected_kpis=("Campaign performance", "Email performance", "Reporting freshness"),
    ),
)


def build_marketing_data_readiness(
    connector_setup: Iterable[dict[str, Any]],
    connector_configs: Iterable[Any],
    *,
    connector_contracts: Iterable[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build mapping, backfill, and KPI-readiness projections."""

    now = _ensure_aware(now) or datetime.now(UTC)
    setup_rows = list(connector_setup)
    config_rows = list(connector_configs)
    field_mappings = build_marketing_field_mapping_status(
        setup_rows,
        config_rows,
        now=now,
    )
    backfills = build_marketing_backfill_status(setup_rows, config_rows, now=now)
    field_summary = summarize_field_mapping_status(field_mappings)
    backfill_summary = summarize_backfill_status(backfills)
    kpi_readiness = build_kpi_readiness(
        field_mappings,
        backfills,
        field_summary,
        backfill_summary,
        setup_rows,
        connector_contracts=connector_contracts or (),
    )
    return {
        "field_mapping_status": field_mappings,
        "field_mapping_summary": field_summary,
        "backfill_status": backfills,
        "backfill_summary": backfill_summary,
        "kpi_readiness": kpi_readiness,
    }


def build_marketing_field_mapping_status(
    connector_setup: Iterable[dict[str, Any]],
    connector_configs: Iterable[Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Validate canonical CMO field mappings from ConnectorConfig.config."""

    now = _ensure_aware(now) or datetime.now(UTC)
    setup_rows = list(connector_setup)
    configs_by_key = _configs_by_key(connector_configs)
    return [
        _build_field_mapping_row(requirement, setup_rows, configs_by_key, now)
        for requirement in FIELD_MAPPING_REQUIREMENTS
    ]


def build_marketing_backfill_status(
    connector_setup: Iterable[dict[str, Any]],
    connector_configs: Iterable[Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Project historical backfill state for each configured marketing source."""

    configs_by_key = _configs_by_key(connector_configs)
    rows: list[dict[str, Any]] = []

    for setup_row in connector_setup:
        key = str(setup_row.get("key") or "").strip().lower()
        if not key:
            continue
        if str(setup_row.get("configured_status") or "") == "unconfigured":
            continue

        payload = _backfill_payload(_config_dict(configs_by_key.get(key)))
        health = str(setup_row.get("health_status") or "").strip().lower()
        if health in BLOCKED_CONNECTOR_HEALTH:
            rows.append(
                _backfill_row(
                    setup_row,
                    "blocked",
                    payload,
                    blocking_reason=(
                        setup_row.get("detail")
                        or "Connector is not ready for historical CMO backfill."
                    ),
                )
            )
            continue

        status = str(payload.get("status") or "").strip().lower()
        if not payload:
            rows.append(_backfill_row(setup_row, "not_started", payload))
        elif status not in BACKFILL_STATES:
            rows.append(
                _backfill_row(
                    setup_row,
                    "blocked",
                    payload,
                    blocking_reason="Backfill status is missing or not recognized.",
                )
            )
        else:
            rows.append(_backfill_row(setup_row, status, payload))

    return rows


def summarize_field_mapping_status(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    counts = dict.fromkeys(FIELD_MAPPING_STATES, 0)
    for row in items:
        status = str(row.get("status") or "")
        if status in counts:
            counts[status] += 1

    blocking = [
        row
        for row in items
        if row.get("missing_blocks") and row.get("status") in {"unmapped", "invalid", "blocked"}
    ]
    degraded = [
        row
        for row in items
        if row.get("status") in {"partially_mapped", "stale"}
        or (not row.get("missing_blocks") and row.get("status") in {"unmapped", "invalid", "blocked"})
    ]
    return {
        "total": len(items),
        **counts,
        "needs_action": sum(1 for row in items if row.get("next_action_cta") != "none"),
        "readiness": "blocked" if blocking else "degraded" if degraded else "ready",
        "blocking": len(blocking),
        "degraded": len(degraded),
    }


def summarize_backfill_status(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    counts = dict.fromkeys(BACKFILL_STATES, 0)
    for row in items:
        status = str(row.get("status") or "")
        if status in counts:
            counts[status] += 1

    if not items:
        readiness = "blocked"
    elif counts["blocked"] or counts["failed"]:
        readiness = "blocked"
    elif counts["completed"] == len(items):
        readiness = "ready"
    else:
        readiness = "degraded"

    return {
        "total": len(items),
        **counts,
        "needs_action": sum(1 for row in items if row.get("next_action_cta") != "none"),
        "readiness": readiness,
    }


def build_kpi_readiness(
    field_mapping_rows: Iterable[dict[str, Any]],
    backfill_rows: Iterable[dict[str, Any]],
    field_summary: dict[str, Any],
    backfill_summary: dict[str, Any],
    connector_setup: Iterable[dict[str, Any]] = (),
    connector_contracts: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    field_items = list(field_mapping_rows)
    backfill_items = list(backfill_rows)
    setup_items = list(connector_setup)
    contract_items = list(connector_contracts)
    blocked_reasons: list[str] = []
    degraded_reasons: list[str] = []
    affected_kpis: set[str] = set()

    for row in field_items:
        status = str(row.get("status") or "")
        affected_kpis.update(str(kpi) for kpi in row.get("affected_kpis") or [])
        if row.get("missing_blocks") and status in {"unmapped", "invalid", "blocked"}:
            blocked_reasons.append(
                f"{row.get('name')} mapping is {status.replace('_', ' ')}."
            )
        elif status in {"partially_mapped", "stale"}:
            degraded_reasons.append(
                f"{row.get('name')} mapping is {status.replace('_', ' ')}."
            )
        elif not row.get("missing_blocks") and status in {"unmapped", "invalid", "blocked"}:
            degraded_reasons.append(
                f"{row.get('name')} mapping is {status.replace('_', ' ')}."
            )

    if not backfill_items:
        blocked_reasons.append("No connected marketing source has a historical backfill record.")
    for row in backfill_items:
        status = str(row.get("status") or "")
        if status in {"blocked", "failed"}:
            blocked_reasons.append(
                f"{row.get('source_name')} backfill is {status}."
            )
        elif status in {"not_started", "queued", "running", "partial"}:
            degraded_reasons.append(
                f"{row.get('source_name')} backfill is {status.replace('_', ' ')}."
            )
    for row in setup_items:
        if str(row.get("configured_status") or "") == "unconfigured":
            continue
        health = str(row.get("health_status") or "")
        if health in {"stale", "degraded"}:
            degraded_reasons.append(
                f"{row.get('name')} connector is {health.replace('_', ' ')}."
            )
    for row in contract_items:
        if str(row.get("configured_status") or "") == "unconfigured":
            continue
        degraded_mode = row.get("degraded_mode") if isinstance(row.get("degraded_mode"), dict) else {}
        reason = degraded_mode.get("reason") or row.get("degraded_mode_reason")
        connector_name = row.get("name") or row.get("connector_key")
        affected_kpis.update(str(kpi) for kpi in degraded_mode.get("affected_kpis") or [])
        if row.get("blocks_production_kpi_confidence"):
            blocked_reasons.append(
                f"{connector_name} connector blocks production KPI confidence: {reason or row.get('failure_class')}."
            )
        elif degraded_mode.get("status") == "degraded" or row.get("read_status") == "degraded":
            degraded_reasons.append(
                f"{connector_name} connector is degraded: {reason or row.get('failure_class')}."
            )

    if blocked_reasons:
        status = "blocked"
        next_action_cta = "resolve_data_readiness"
    elif degraded_reasons or field_summary.get("readiness") != "ready" or backfill_summary.get("readiness") != "ready":
        status = "degraded"
        next_action_cta = "review_data_readiness"
    else:
        status = "ready"
        next_action_cta = "none"

    historical_status = "ready" if backfill_summary.get("readiness") == "ready" else "blocked"
    return {
        "status": status,
        "historical_status": historical_status,
        "field_mapping_readiness": field_summary.get("readiness"),
        "backfill_readiness": backfill_summary.get("readiness"),
        "blocked_reasons": blocked_reasons,
        "degraded_reasons": degraded_reasons,
        "affected_kpis": sorted(affected_kpis),
        "degraded_mode": summarize_degraded_modes(contract_items),
        "next_action_cta": next_action_cta,
    }


def _build_field_mapping_row(
    requirement: FieldMappingRequirement,
    setup_rows: list[dict[str, Any]],
    configs_by_key: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    candidate_sources = _candidate_sources(requirement, setup_rows)
    active_sources = [
        row
        for row in candidate_sources
        if str(row.get("health_status") or "").strip().lower() in ACTIVE_CONNECTOR_HEALTH
    ]
    source_names = [str(row.get("name") or row.get("key")) for row in active_sources]

    if requirement.source_categories and not active_sources:
        return _field_mapping_row(
            requirement,
            "blocked",
            [],
            missing_fields=list(requirement.required_fields),
            blocking_reason=(
                "No configured source in "
                f"{', '.join(requirement.source_categories)} is ready for mapping."
            ),
            next_action_cta="connect_source",
        )

    if requirement.source_categories:
        payloads = [
            (
                str(source.get("key") or "").strip().lower(),
                _mapping_payload(
                    _config_dict(configs_by_key.get(str(source.get("key") or "").strip().lower())),
                    requirement.key,
                ),
            )
            for source in active_sources
        ]
    else:
        payloads = [
            (key, _mapping_payload(_config_dict(config), requirement.key))
            for key, config in configs_by_key.items()
        ]

    payloads = [(key, payload) for key, payload in payloads if payload]
    if not payloads:
        return _field_mapping_row(
            requirement,
            "unmapped",
            source_names,
            missing_fields=list(requirement.required_fields),
            next_action_cta="map_fields",
        )

    explicit_block = _first_blocking_reason(payload for _key, payload in payloads)
    if explicit_block:
        return _field_mapping_row(
            requirement,
            "blocked",
            source_names,
            missing_fields=list(requirement.required_fields),
            blocking_reason=explicit_block,
            next_action_cta="resolve_mapping_blocker",
        )

    combined = _merge_payloads(payload for _key, payload in payloads)
    invalid_reason = _invalid_mapping_reason(requirement, combined)
    if invalid_reason:
        return _field_mapping_row(
            requirement,
            "invalid",
            source_names,
            missing_fields=[],
            blocking_reason=invalid_reason,
            next_action_cta="fix_mapping",
            last_updated_at=_latest_updated_at(payloads),
        )

    missing_fields = _missing_required_fields(requirement.required_fields, combined)
    missing_source_count = 0
    if requirement.source_categories:
        mapped_keys = {key for key, _payload in payloads}
        missing_source_count = sum(
            1
            for source in active_sources
            if str(source.get("key") or "").strip().lower() not in mapped_keys
        )

    if len(missing_fields) == len(requirement.required_fields):
        status = "unmapped"
        next_action = "map_fields"
    elif missing_fields or missing_source_count:
        status = "partially_mapped"
        next_action = "complete_mapping"
    elif _is_stale(payloads, now):
        status = "stale"
        next_action = "review_mapping"
    else:
        status = "valid"
        next_action = "none"

    return _field_mapping_row(
        requirement,
        status,
        source_names,
        missing_fields=missing_fields,
        next_action_cta=next_action,
        last_updated_at=_latest_updated_at(payloads),
    )


def _field_mapping_row(
    requirement: FieldMappingRequirement,
    status: str,
    source_names: list[str],
    *,
    missing_fields: list[str],
    next_action_cta: str,
    blocking_reason: str | None = None,
    last_updated_at: str | None = None,
) -> dict[str, Any]:
    normalized_status = status if status in FIELD_MAPPING_STATES else "blocked"
    escalation_decision = (
        evaluate_marketing_escalation(
            {
                "trigger_type": "data_mapping_blocked",
                "mapping_status": normalized_status,
                "action": "field_mapping",
                "step_id": requirement.key,
                "severity": "high",
                "reason": blocking_reason or f"{requirement.name} mapping is {normalized_status}.",
            }
        )
        if requirement.missing_blocks and normalized_status in {"unmapped", "invalid", "blocked"}
        else None
    )
    row = {
        "key": requirement.key,
        "name": requirement.name,
        "status": normalized_status,
        "source_categories": list(requirement.source_categories),
        "sources": source_names,
        "required_fields": list(requirement.required_fields),
        "missing_fields": missing_fields,
        "affected_kpis": list(requirement.affected_kpis),
        "missing_blocks": requirement.missing_blocks,
        "last_updated_at": last_updated_at,
        "blocking_reason": blocking_reason,
        "next_action_cta": next_action_cta,
        "escalation_decision": escalation_decision,
        "escalation_evidence": escalation_decision.get("evidence") if escalation_decision else None,
    }
    if escalation_decision is not None:
        audit_package = build_cmo_decision_audit_package(
            {
                "event_type": "connector_degraded_failure_decision",
                "decision_type": normalized_status,
                "action": "field_mapping",
                "step_id": requirement.key,
                "escalation_result": escalation_decision,
                "rationale": blocking_reason or f"{requirement.name} mapping is {normalized_status}.",
                "risk_flags": [normalized_status, "data_mapping_blocked"],
                "final_outcome": normalized_status,
                "actor_type": "system",
            }
        )
        row["decision_audit"] = audit_package
        row["decision_audit_ref"] = audit_package["audit_reference"]
    return row


def _backfill_row(
    setup_row: dict[str, Any],
    status: str,
    payload: dict[str, Any],
    *,
    blocking_reason: str | None = None,
) -> dict[str, Any]:
    status = status if status in BACKFILL_STATES else "blocked"
    reason = blocking_reason or str(payload.get("blocking_reason") or "").strip() or None
    escalation_decision = (
        evaluate_marketing_escalation(
            {
                "trigger_type": "backfill_failed",
                "is_backfill": True,
                "backfill_status": status,
                "connector_key": setup_row.get("key"),
                "workflow_id": payload.get("workflow_id"),
                "severity": "high",
                "reason": reason or f"{setup_row.get('name')} backfill is {status}.",
            }
        )
        if status in {"failed", "blocked"}
        else None
    )
    row = {
        "source_connector_key": setup_row.get("key"),
        "source_name": setup_row.get("name"),
        "category": setup_row.get("category"),
        "status": status,
        "requested_start": _string_or_none(
            payload.get("requested_start") or payload.get("date_range_start")
        ),
        "requested_end": _string_or_none(
            payload.get("requested_end") or payload.get("date_range_end")
        ),
        "records_discovered": _int_or_none(payload.get("records_discovered")),
        "records_imported": _int_or_none(payload.get("records_imported")),
        "records_skipped": _int_or_none(payload.get("records_skipped")),
        "records_failed": _int_or_none(payload.get("records_failed")),
        "last_run_at": _iso_or_string(payload.get("last_run_at")),
        "blocking_reason": reason,
        "next_action_cta": _backfill_cta(status),
        "escalation_decision": escalation_decision,
        "escalation_evidence": escalation_decision.get("evidence") if escalation_decision else None,
    }
    if escalation_decision is not None:
        audit_package = build_cmo_decision_audit_package(
            {
                "event_type": "connector_degraded_failure_decision",
                "decision_type": status,
                "connector_key": setup_row.get("key"),
                "source_refs": [setup_row.get("key")],
                "escalation_result": escalation_decision,
                "rationale": reason or f"{setup_row.get('name')} backfill is {status}.",
                "risk_flags": [status, "backfill_failed"],
                "final_outcome": status,
                "actor_type": "system",
            }
        )
        row["decision_audit"] = audit_package
        row["decision_audit_ref"] = audit_package["audit_reference"]
    return row


def _candidate_sources(
    requirement: FieldMappingRequirement,
    setup_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not requirement.source_categories:
        return []
    allowed = set(requirement.source_categories)
    return [
        row
        for row in setup_rows
        if str(row.get("category") or "").strip() in allowed
    ]


def _configs_by_key(connector_configs: Iterable[Any]) -> dict[str, Any]:
    return {
        str(getattr(config, "connector_name", "") or "").strip().lower(): config
        for config in connector_configs
    }


def _config_dict(config: Any | None) -> dict[str, Any]:
    value = getattr(config, "config", None)
    return value if isinstance(value, dict) else {}


def _mapping_payload(config: dict[str, Any], key: str) -> dict[str, Any]:
    root = (
        config.get("marketing_field_mapping")
        or config.get("marketing_field_mappings")
        or config.get("field_mapping")
        or config.get("field_mappings")
        or {}
    )
    if not isinstance(root, dict):
        root = {}
    value = root.get(key)
    if isinstance(value, dict):
        return value
    if key == "currency":
        value = root.get("currency") or config.get("currency") or config.get("default_currency")
        if isinstance(value, str):
            return {"currency": value, "updated_at": root.get("updated_at")}
    if key == "timezone":
        value = root.get("timezone") or config.get("timezone")
        if isinstance(value, str):
            return {"timezone": value, "updated_at": root.get("updated_at")}
    if key == "fiscal_calendar":
        value = (
            root.get("fiscal_year_start_month")
            or config.get("fiscal_year_start_month")
            or config.get("fiscal_calendar_start_month")
        )
        if value is not None:
            return {"fiscal_year_start_month": value, "updated_at": root.get("updated_at")}
    return {}


def _backfill_payload(config: dict[str, Any]) -> dict[str, Any]:
    value = (
        config.get("marketing_backfill")
        or config.get("backfill")
        or config.get("historical_backfill")
        or {}
    )
    return value if isinstance(value, dict) else {}


def _first_blocking_reason(payloads: Iterable[dict[str, Any]]) -> str | None:
    for payload in payloads:
        reason = str(payload.get("blocking_reason") or payload.get("blocked_reason") or "").strip()
        status = str(payload.get("status") or payload.get("mapping_status") or "").strip().lower()
        if reason:
            return reason
        if status == "blocked":
            return "Mapping is explicitly blocked."
    return None


def _merge_payloads(payloads: Iterable[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for payload in payloads:
        for key, value in payload.items():
            if value not in (None, "", [], {}):
                merged[key] = value
    return merged


def _missing_required_fields(required_fields: tuple[str, ...], payload: dict[str, Any]) -> list[str]:
    return [
        field
        for field in required_fields
        if payload.get(field) in (None, "", [], {})
    ]


def _invalid_mapping_reason(
    requirement: FieldMappingRequirement,
    payload: dict[str, Any],
) -> str | None:
    if requirement.key == "currency":
        currency = str(payload.get("currency") or "").strip().upper()
        if currency and currency not in VALID_CURRENCIES:
            return f"Currency '{currency}' is not in the supported ISO currency allowlist."
    elif requirement.key == "timezone":
        timezone = str(payload.get("timezone") or "").strip()
        if timezone:
            try:
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError:
                return f"Timezone '{timezone}' is not recognized."
    elif requirement.key == "fiscal_calendar":
        raw = payload.get("fiscal_year_start_month")
        try:
            month = int(raw)
        except (TypeError, ValueError):
            return "Fiscal year start month must be an integer between 1 and 12."
        if month < 1 or month > 12:
            return "Fiscal year start month must be between 1 and 12."
    return None


def _latest_updated_at(payloads: list[tuple[str, dict[str, Any]]]) -> str | None:
    timestamps = [
        value
        for _key, payload in payloads
        for value in (_iso_or_string(payload.get("updated_at") or payload.get("last_validated_at")),)
        if value
    ]
    return max(timestamps) if timestamps else None


def _is_stale(payloads: list[tuple[str, dict[str, Any]]], now: datetime) -> bool:
    timestamps = [
        parsed
        for _key, payload in payloads
        for parsed in (_parse_datetime(payload.get("updated_at") or payload.get("last_validated_at")),)
        if parsed is not None
    ]
    return bool(timestamps) and all(now - timestamp > MAPPING_STALE_AFTER for timestamp in timestamps)


def _backfill_cta(status: str) -> str:
    return {
        "not_started": "start_backfill",
        "queued": "monitor_backfill",
        "running": "monitor_backfill",
        "completed": "none",
        "partial": "review_failed_records",
        "failed": "retry_backfill",
        "blocked": "resolve_blocker",
    }[status]


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _ensure_aware(parsed)
    return None


def _iso_or_string(value: Any) -> str | None:
    if isinstance(value, datetime):
        return (_ensure_aware(value) or value).isoformat()
    if isinstance(value, str) and value.strip():
        return value
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
