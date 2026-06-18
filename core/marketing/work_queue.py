"""CMO operator work queue projection.

CMO-8.1 turns the readiness, governance, KPI, reconciliation, and report-gate
projections into prioritized operator-visible work items. The queue is
deterministic and storage-free by design; it can later be persisted without
changing the API shape.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

CMO_WORK_QUEUE_VERSION = "2026-05-23.cmo-8.1"

SEVERITIES = ("critical", "high", "medium", "low", "info")
ITEM_STATUSES = ("open", "blocked", "waiting", "resolved", "dismissed")

SEVERITY_SCORE = {
    "critical": 1000,
    "high": 800,
    "medium": 500,
    "low": 250,
    "info": 50,
}
CATEGORY_SCORE = {
    "crisis_risk": 120,
    "external_write": 110,
    "approval": 100,
    "escalation": 95,
    "connector": 85,
    "policy": 82,
    "audit": 80,
    "report": 75,
    "workflow": 70,
    "kpi": 62,
    "reconciliation": 60,
    "data_readiness": 55,
    "source_data": 50,
}

BLOCKING_CONNECTOR_STATES = {  # enterprise-gate: process-local-ok reason=static-work-queue-state-set
    "missing",
    "unconfigured",
    "expired_auth",
    "auth_expired",
    "insufficient_scope",
    "missing_scope",
    "connector_disabled",
    "malformed_payload",
    "quota_exhausted",
}
DEGRADED_CONNECTOR_STATES = {  # enterprise-gate: process-local-ok reason=static-work-queue-state-set
    "stale",
    "stale_data",
    "degraded",
    "partial",
    "partial_data",
    "rate_limited",
    "timeout",
    "vendor_5xx",
}
# enterprise-gate: process-local-ok reason=static-work-queue-state-set
BAD_MAPPING_STATES = {"unmapped", "invalid", "blocked", "partially_mapped", "stale"}
# enterprise-gate: process-local-ok reason=static-work-queue-state-set
BAD_BACKFILL_STATES = {"not_started", "partial", "failed", "blocked", "queued", "running"}
# enterprise-gate: process-local-ok reason=static-work-queue-state-set
BAD_WORKFLOW_STATES = {"promotion_blocked", "unavailable", "degraded", "paused", "shadow", "promotion_ready"}
# enterprise-gate: process-local-ok reason=static-work-queue-state-set
BAD_KPI_STATES = {"blocked", "unavailable", "degraded"}
# enterprise-gate: process-local-ok reason=static-work-queue-state-set
BAD_RECONCILIATION_STATES = {"failed", "blocked", "warning", "unavailable"}
# enterprise-gate: process-local-ok reason=static-work-queue-state-set
BAD_REPORT_STATES = {"blocked", "unavailable", "warning"}
# enterprise-gate: process-local-ok reason=static-work-queue-state-set
BAD_WRITE_STATES = {"rejected", "timeout_unknown", "write_unconfirmed", "retry_scheduled", "accepted"}


def build_cmo_work_queue_projection(
    *,
    approval_timeout_risk: Mapping[str, Any] | None = None,
    escalation_projection: Mapping[str, Any] | None = None,
    connector_setup: Iterable[Mapping[str, Any]] = (),
    connector_contracts: Iterable[Mapping[str, Any]] = (),
    data_readiness: Mapping[str, Any] | None = None,
    workflow_activation: Mapping[str, Any] | None = None,
    policy_projection: Mapping[str, Any] | None = None,
    decision_audit_projection: Mapping[str, Any] | None = None,
    kpi_results: Iterable[Mapping[str, Any]] = (),
    reconciliation_checks: Iterable[Mapping[str, Any]] = (),
    report_quality_gates: Iterable[Mapping[str, Any]] = (),
    external_write_results: Iterable[Mapping[str, Any]] = (),
    source_context: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build prioritized CMO work items from current CMO projections."""

    timestamp = _ensure_aware(now) or datetime.now(UTC)
    source = source_context if isinstance(source_context, Mapping) else {}
    data = data_readiness if isinstance(data_readiness, Mapping) else {}
    workflow = workflow_activation if isinstance(workflow_activation, Mapping) else {}

    items: dict[str, dict[str, Any]] = {}
    _collect_approval_items(items, approval_timeout_risk, timestamp)
    _collect_escalation_items(items, approval_timeout_risk, escalation_projection, source, timestamp)
    _collect_connector_setup_items(items, connector_setup, timestamp)
    _collect_connector_contract_items(items, connector_contracts, timestamp)
    _collect_data_readiness_items(items, data, timestamp)
    _collect_workflow_items(items, workflow, timestamp)
    _collect_policy_and_audit_items(
        items,
        policy_projection,
        decision_audit_projection,
        workflow,
        source,
        timestamp,
    )
    _collect_external_write_items(
        items,
        [*list(external_write_results), *_external_write_results_from_source(source)],
        timestamp,
    )
    _collect_kpi_items(items, kpi_results, timestamp)
    _collect_reconciliation_items(items, reconciliation_checks, timestamp)
    _collect_report_quality_items(items, report_quality_gates, timestamp)
    _collect_source_context_items(items, source, timestamp)
    _collect_crisis_risk_items(items, source, timestamp)

    queue = _sort_items(items.values())
    return {
        "cmo_work_queue_version": CMO_WORK_QUEUE_VERSION,
        "cmo_work_queue": queue,
        "cmo_work_queue_summary": summarize_cmo_work_queue(queue),
    }


def build_cmo_work_queue_projection_from_kpi_payload(
    payload: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the work queue from a `/kpis/cmo` response payload."""

    data = payload if isinstance(payload, Mapping) else {}
    workflow_activation = {
        "workflow_activation_status": _dicts(data.get("workflow_activation_status")),
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
    approval_timeout_risk = data.get("approval_timeout_risk")
    return build_cmo_work_queue_projection(
        approval_timeout_risk=approval_timeout_risk if isinstance(approval_timeout_risk, Mapping) else {},
        escalation_projection=escalation_projection,
        connector_setup=_dicts(data.get("connector_setup")),
        connector_contracts=_dicts(data.get("connector_contracts")),
        data_readiness={
            "field_mapping_status": _dicts(data.get("field_mapping_status")),
            "backfill_status": _dicts(data.get("backfill_status")),
            "kpi_readiness": data.get("kpi_readiness") or {},
        },
        workflow_activation=workflow_activation,
        policy_projection=policy_projection,
        decision_audit_projection=decision_audit_projection,
        kpi_results=_dicts(data.get("unified_cmo_kpi_results")),
        reconciliation_checks=_dicts(data.get("cmo_kpi_reconciliation_checks")),
        report_quality_gates=_dicts(data.get("report_quality_gates")),
        external_write_results=_external_write_results_from_source(data),
        source_context=data,
        now=now,
    )


def summarize_cmo_work_queue(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    queue = [dict(item) for item in items if isinstance(item, Mapping)]
    severity_counts = dict.fromkeys(SEVERITIES, 0)
    status_counts = dict.fromkeys(ITEM_STATUSES, 0)
    category_counts: dict[str, int] = {}
    for item in queue:
        severity = str(item.get("severity") or "info")
        status = str(item.get("status") or "open")
        category = str(item.get("category") or "unknown")
        if severity in severity_counts:
            severity_counts[severity] += 1
        if status in status_counts:
            status_counts[status] += 1
        category_counts[category] = category_counts.get(category, 0) + 1

    first = queue[0] if queue else None
    readiness = (
        "blocked"
        if severity_counts["critical"] or severity_counts["high"] or status_counts["blocked"]
        else "degraded"
        if queue
        else "ready"
    )
    return {
        "schema_version": CMO_WORK_QUEUE_VERSION,
        "total": len(queue),
        "readiness": readiness,
        "critical_or_high": severity_counts["critical"] + severity_counts["high"],
        "needs_action": sum(1 for item in queue if item.get("next_action_key") not in {None, "", "none"}),
        "top_priority_score": int(first.get("priority_score") or 0) if first else 0,
        "first_item_id": first.get("item_id") if first else None,
        "next_action_cta": first.get("next_action_cta") if first else _cta("none"),
        "by_severity": severity_counts,
        "by_status": status_counts,
        "by_category": dict(sorted(category_counts.items())),
        "empty_state": (
            "No open CMO work queue items. This does not upgrade stub, unavailable, or demo capabilities."
            if not queue
            else None
        ),
    }


def _collect_approval_items(
    items: dict[str, dict[str, Any]],
    approval_timeout_risk: Mapping[str, Any] | None,
    now: datetime,
) -> None:
    risk = approval_timeout_risk if isinstance(approval_timeout_risk, Mapping) else {}
    for decision in _dicts(risk.get("approval_timeout_decisions")):
        approval_id = _string_or_none(decision.get("approval_id")) or "unknown_approval"
        timed_out = bool(decision.get("timed_out"))
        due_at = _string_or_none(decision.get("due_at"))
        approval_type = _string_or_none(decision.get("approval_type")) or "approval"
        owner = _string_or_none(
            decision.get("escalation_target")
            or decision.get("requested_approver_role")
            or decision.get("policy_required_role")
        ) or "cmo"
        if timed_out:
            severity = "critical" if decision.get("external_writes_allowed") is False else "high"
            _add_item(
                items,
                dedupe_key=f"approval:{approval_id}",
                category="approval",
                item_type="approval_timeout",
                severity=severity,
                status="blocked",
                title=f"Overdue CMO approval: {approval_type.replace('_', ' ')}",
                message=str(
                    decision.get("safe_fallback_message")
                    or "Approval is overdue; customer-facing marketing action must stay blocked until resolved."
                ),
                owner_role=owner,
                due_at=due_at,
                action_key=str(decision.get("next_action_cta") or "resolve_overdue_approvals"),
                affected_workflow=_string_or_none(decision.get("workflow_id")),
                source_refs=[_approval_ref(decision)],
                audit_refs=_audit_refs(decision),
                created_at=_string_or_none(decision.get("created_at")),
                now=now,
                customer_facing=True,
            )
        elif decision.get("status") == "pending":
            due = _parse_datetime(due_at)
            severity = "high" if due is not None and due <= now + timedelta(hours=2) else "medium"
            _add_item(
                items,
                dedupe_key=f"approval:{approval_id}",
                category="approval",
                item_type="pending_approval",
                severity=severity,
                status="waiting",
                title=f"Pending CMO approval: {approval_type.replace('_', ' ')}",
                message="Human approval is waiting; external/customer-facing action remains blocked until approval.",
                owner_role=owner,
                due_at=due_at,
                action_key="review_pending_approvals",
                affected_workflow=_string_or_none(decision.get("workflow_id")),
                source_refs=[_approval_ref(decision)],
                created_at=_string_or_none(decision.get("created_at")),
                now=now,
                customer_facing=True,
            )


def _collect_escalation_items(
    items: dict[str, dict[str, Any]],
    approval_timeout_risk: Mapping[str, Any] | None,
    escalation_projection: Mapping[str, Any] | None,
    source: Mapping[str, Any],
    now: datetime,
) -> None:
    decisions: list[dict[str, Any]] = []
    risk = approval_timeout_risk if isinstance(approval_timeout_risk, Mapping) else {}
    for approval in _dicts(risk.get("approval_timeout_decisions")):
        decisions.extend(_dicts([approval.get("escalation_decision")]))
    decisions.extend(_dicts(source.get("marketing_escalation_decisions")))
    decisions.extend(_dicts(source.get("escalation_decisions")))
    decisions.extend(_dicts(source.get("recent_escalations")))

    summary = (
        escalation_projection.get("marketing_escalation_summary")
        if isinstance(escalation_projection, Mapping)
        else None
    )
    if isinstance(summary, Mapping) and summary.get("status") == "missing_route":
        _add_item(
            items,
            dedupe_key="escalation:missing_routes",
            category="escalation",
            item_type="missing_escalation_route",
            severity="high",
            status="blocked",
            title="CMO escalation matrix is missing routes",
            message="Production workflows with escalation-sensitive actions cannot proceed without a configured route.",
            owner_role="cmo",
            action_key=str(summary.get("next_action_cta") or "configure_escalation_matrix"),
            source_refs=[{"type": "marketing_escalation_summary", "status": summary.get("status")}],
            now=now,
        )

    for decision in decisions:
        if not decision or str(decision.get("decision") or "") == "no_escalation":
            continue
        event_id = _string_or_none(decision.get("event_id")) or _stable_key("escalation", decision)
        trigger = _string_or_none(decision.get("trigger_type") or decision.get("event_type")) or "escalation"
        severity = _severity(decision.get("severity"), default="high")
        _add_item(
            items,
            dedupe_key=f"escalation:{event_id}:{trigger}",
            category="escalation",
            item_type="escalation",
            severity=severity,
            status="open",
            title=f"CMO escalation: {trigger.replace('_', ' ')}",
            message=str(decision.get("reason") or "Escalation route requires owner attention."),
            owner_role=_string_or_none(decision.get("escalation_target")) or "cmo",
            due_at=_string_or_none(decision.get("due_at") or decision.get("sla_due_at")),
            action_key=str(decision.get("next_action_cta") or "review_escalation"),
            affected_workflow=_string_or_none(decision.get("workflow_id")),
            source_refs=[{"type": "escalation_decision", "event_id": event_id, "trigger_type": trigger}],
            audit_refs=_audit_refs(decision),
            now=now,
            customer_facing=trigger in {"crisis_public_response", "external_write_timeout_unknown"},
        )


def _collect_connector_setup_items(
    items: dict[str, dict[str, Any]],
    connector_setup: Iterable[Mapping[str, Any]],
    now: datetime,
) -> None:
    for row in _dicts(connector_setup):
        key = _string_or_none(row.get("key") or row.get("connector_key"))
        if not key:
            continue
        health = _normalize_key(row.get("health_status") or row.get("status"))
        configured = _normalize_key(row.get("configured_status") or row.get("setup_status"))
        cta = _normalize_key(row.get("cta_state") or row.get("next_action_cta"))
        coverage = _normalize_key(row.get("data_coverage_status"))
        if (
            health in {"healthy", "ready"}
            and configured not in {"unconfigured", "missing"}
            and cta in {"", "none"}
            and coverage in {"", "ready"}
        ):
            continue
        state = health or configured or coverage or "unknown"
        severity = _connector_severity(state, bool(row.get("write_capabilities")))
        _add_item(
            items,
            dedupe_key=f"connector:{key}",
            category="connector",
            item_type="connector_issue",
            severity=severity,
            status="blocked" if severity in {"critical", "high"} else "open",
            title=f"{row.get('name') or key} connector needs attention",
            message=str(row.get("detail") or f"Connector state is {state.replace('_', ' ')}."),
            owner_role=_string_or_none(row.get("owner")) or "marketing_ops",
            due_at=_string_or_none(row.get("due_at")),
            action_key=cta or _connector_action(state),
            affected_connector=key,
            source_refs=[_connector_setup_ref(row, state)],
            now=now,
        )


def _collect_connector_contract_items(
    items: dict[str, dict[str, Any]],
    connector_contracts: Iterable[Mapping[str, Any]],
    now: datetime,
) -> None:
    for row in _dicts(connector_contracts):
        key = _string_or_none(row.get("connector_key"))
        if not key:
            continue
        contract_state = _normalize_key(row.get("contract_state"))
        read_status = _normalize_key(row.get("read_status"))
        write_status = _normalize_key(row.get("write_status"))
        confirmation_status = _normalize_key(row.get("external_write_confirmation_status"))
        failure_class = _normalize_key(row.get("failure_class"))
        blocks_write = bool(row.get("blocks_external_writes"))
        blocks_kpi = bool(row.get("blocks_production_kpi_confidence"))
        has_write_capability = bool(_list_from_value(row.get("write_capabilities")))
        bad = (
            contract_state in BLOCKING_CONNECTOR_STATES | DEGRADED_CONNECTOR_STATES
            or read_status in {"blocked", "degraded"}
            or (has_write_capability and write_status not in {"ready", ""})
            or confirmation_status == "write_unconfirmed"
            or failure_class
            or blocks_write
            or blocks_kpi
            or bool(row.get("mock_or_test_double"))
        )
        if not bad:
            continue
        severity = _connector_severity(
            contract_state or read_status or write_status,
            has_write_capability or blocks_write,
        )
        if confirmation_status == "write_unconfirmed" or blocks_write:
            severity = "high"
        if bool(row.get("mock_or_test_double")):
            severity = "high"
        _add_item(
            items,
            dedupe_key=f"connector:{key}",
            category="connector",
            item_type="connector_contract_issue",
            severity=severity,
            status="blocked" if severity in {"critical", "high"} else "open",
            title=f"{row.get('name') or key} connector contract is not production-ready",
            message=str(
                row.get("degraded_mode_reason")
                or row.get("reason")
                or f"Read status {read_status or 'unknown'}, write status {write_status or 'unknown'}."
            ),
            owner_role="marketing_ops",
            action_key=str(row.get("next_action_cta") or _connector_action(contract_state or write_status)),
            affected_connector=key,
            source_refs=[_connector_contract_ref(row)],
            audit_refs=_audit_refs(row),
            now=now,
            customer_facing=blocks_write,
        )


def _collect_data_readiness_items(
    items: dict[str, dict[str, Any]],
    data_readiness: Mapping[str, Any],
    now: datetime,
) -> None:
    for row in _dicts(data_readiness.get("field_mapping_status")):
        status = _normalize_key(row.get("status"))
        if status not in BAD_MAPPING_STATES:
            continue
        key = _string_or_none(row.get("key")) or "unknown_mapping"
        severity = "high" if status in {"invalid", "blocked", "unmapped"} else "medium"
        _add_item(
            items,
            dedupe_key=f"mapping:{key}",
            category="data_readiness",
            item_type="mapping_blocker",
            severity=severity,
            status="blocked" if severity == "high" else "open",
            title=f"Field mapping needs action: {row.get('name') or key}",
            message=str(row.get("blocking_reason") or f"Mapping status is {status.replace('_', ' ')}."),
            owner_role="revops",
            due_at=_string_or_none(row.get("due_at")),
            action_key=str(row.get("next_action_cta") or "map_fields"),
            affected_kpi=", ".join(_string_list(row.get("affected_kpis"))) or None,
            source_refs=[{"type": "field_mapping", "key": key, "status": status}],
            audit_refs=_audit_refs(row),
            created_at=_string_or_none(row.get("created_at") or row.get("last_updated_at")),
            now=now,
        )

    for row in _dicts(data_readiness.get("backfill_status")):
        status = _normalize_key(row.get("status"))
        if status not in BAD_BACKFILL_STATES:
            continue
        key = (
            _string_or_none(row.get("source_connector_key"))
            or _string_or_none(row.get("category"))
            or "unknown_source"
        )
        severity = "high" if status in {"failed", "blocked", "not_started"} else "medium"
        _add_item(
            items,
            dedupe_key=f"backfill:{key}",
            category="data_readiness",
            item_type="backfill_blocker",
            severity=severity,
            status="blocked" if severity == "high" else "waiting" if status in {"queued", "running"} else "open",
            title=f"Historical backfill needs action: {row.get('source_name') or key}",
            message=str(row.get("blocking_reason") or f"Backfill status is {status.replace('_', ' ')}."),
            owner_role="revops",
            action_key=str(row.get("next_action_cta") or "complete_backfill"),
            affected_connector=key,
            source_refs=[{"type": "backfill", "source_connector_key": key, "status": status}],
            audit_refs=_audit_refs(row),
            created_at=_string_or_none(row.get("created_at") or row.get("last_run_at")),
            now=now,
        )


def _collect_workflow_items(
    items: dict[str, dict[str, Any]],
    workflow_activation: Mapping[str, Any],
    now: datetime,
) -> None:
    for row in _dicts(workflow_activation.get("workflow_activation_status")):
        state = _normalize_key(row.get("state"))
        if state not in BAD_WORKFLOW_STATES:
            continue
        key = _string_or_none(row.get("workflow_key") or row.get("key")) or "unknown_workflow"
        if state == "promotion_ready":
            severity = "medium"
            status = "open"
            title = f"{row.get('name') or key} is ready for promotion review"
            message = "Workflow prerequisites pass; CMO/admin must explicitly promote it before active writes."
        elif state in {"promotion_blocked", "unavailable", "paused"}:
            severity = "high"
            status = "blocked"
            title = f"{row.get('name') or key} workflow is blocked"
            message = _first_text(row.get("blocked_reasons")) or f"Workflow state is {state.replace('_', ' ')}."
        else:
            severity = "medium"
            status = "open"
            title = f"{row.get('name') or key} workflow needs review"
            message = _first_text(row.get("degraded_reasons")) or _first_text(row.get("blocked_reasons")) or (
                f"Workflow state is {state.replace('_', ' ')}."
            )
        _add_item(
            items,
            dedupe_key=f"workflow:{key}:{state}",
            category="workflow",
            item_type="workflow_activation",
            severity=severity,
            status=status,
            title=title,
            message=message,
            owner_role=_string_or_none(row.get("approval_owner") or row.get("policy_owner")) or "cmo",
            action_key=str(row.get("next_action_cta") or "resolve_promotion_blocker"),
            affected_workflow=key,
            affected_capability=key,
            source_refs=[{"type": "workflow_activation", "workflow_key": key, "state": state}],
            audit_refs=_audit_refs(row),
            created_at=_string_or_none(row.get("evaluated_at")),
            now=now,
        )


def _collect_policy_and_audit_items(
    items: dict[str, dict[str, Any]],
    policy_projection: Mapping[str, Any] | None,
    decision_audit_projection: Mapping[str, Any] | None,
    workflow_activation: Mapping[str, Any],
    source: Mapping[str, Any],
    now: datetime,
) -> None:
    policy_summary = (
        policy_projection.get("marketing_policy_summary")
        if isinstance(policy_projection, Mapping)
        else None
    )
    if isinstance(policy_summary, Mapping) and policy_summary.get("status") == "missing_policy":
        _add_policy_item(
            items,
            dedupe_key="policy:manifest",
            title="Marketing policy manifest is missing",
            message="Active customer-facing CMO actions must fail closed until policy coverage is configured.",
            action_key=str(policy_summary.get("next_action_cta") or "configure_marketing_policy_manifest"),
            workflow_key=None,
            now=now,
        )

    audit_summary = (
        decision_audit_projection.get("marketing_decision_audit_summary")
        if isinstance(decision_audit_projection, Mapping)
        else None
    )
    if isinstance(audit_summary, Mapping) and audit_summary.get("status") == "missing_audit_evidence":
        _add_audit_item(
            items,
            dedupe_key="audit:package",
            title="CMO decision audit package is missing evidence",
            message=(
                "Production customer-facing actions cannot pass readiness without required "
                "decision-audit evidence."
            ),
            action_key=str(audit_summary.get("next_action_cta") or "configure_decision_audit_package"),
            workflow_key=None,
            now=now,
        )

    for row in _dicts(workflow_activation.get("workflow_activation_status")):
        workflow_key = _string_or_none(row.get("workflow_key")) or "unknown_workflow"
        policy = row.get("marketing_policy") if isinstance(row.get("marketing_policy"), Mapping) else {}
        if policy and _normalize_key(policy.get("status")) in {"missing_policy", "blocked"}:
            _add_policy_item(
                items,
                dedupe_key=f"policy:{workflow_key}",
                title=f"{row.get('name') or workflow_key} has a marketing policy blocker",
                message=_first_text(policy.get("missing_policy_actions") or policy.get("blocked_actions"))
                or "Workflow has missing or blocking marketing policy coverage.",
                action_key=str(policy.get("next_action_cta") or "configure_marketing_policy_manifest"),
                workflow_key=workflow_key,
                audit_refs=_audit_refs(policy),
                now=now,
            )
        audit = row.get("decision_audit") if isinstance(row.get("decision_audit"), Mapping) else {}
        if audit and _normalize_key(audit.get("status")) == "missing_audit_evidence":
            _add_audit_item(
                items,
                dedupe_key=f"audit:{workflow_key}",
                title=f"{row.get('name') or workflow_key} lacks decision-audit evidence",
                message=_first_text(audit.get("missing_audit_actions"))
                or "Workflow needs CMO decision audit evidence before production use.",
                action_key=str(audit.get("next_action_cta") or "configure_decision_audit_package"),
                workflow_key=workflow_key,
                audit_refs=_audit_refs(audit),
                now=now,
            )

    for decision in _dicts(source.get("marketing_policy_decisions") or source.get("policy_decisions")):
        outcome = _normalize_key(decision.get("decision"))
        if outcome not in {"missing_policy", "blocked", "read_only_only"}:
            continue
        workflow_key = _string_or_none(decision.get("affected_workflow") or decision.get("workflow_id"))
        _add_policy_item(
            items,
            dedupe_key=f"policy_decision:{workflow_key or 'global'}:{decision.get('affected_action')}",
            title="CMO policy decision blocks execution",
            message=str(decision.get("reason") or f"Policy decision is {outcome}."),
            action_key=str(decision.get("next_action_cta") or "configure_marketing_policy_manifest"),
            workflow_key=workflow_key,
            audit_refs=_audit_refs(decision),
            now=now,
        )


def _collect_external_write_items(
    items: dict[str, dict[str, Any]],
    external_write_results: Iterable[Mapping[str, Any]],
    now: datetime,
) -> None:
    for row in _dicts(external_write_results):
        state = _normalize_key(
            row.get("final_state")
            or row.get("external_write_state")
            or row.get("write_state")
            or row.get("status")
        )
        if state not in BAD_WRITE_STATES:
            continue
        workflow_key = _string_or_none(row.get("workflow_id") or row.get("workflow_key"))
        step_id = _string_or_none(row.get("step_id"))
        connector_key = _string_or_none(row.get("connector_key") or row.get("connector"))
        severity = "critical" if state in {"rejected", "timeout_unknown", "write_unconfirmed", "accepted"} else "high"
        status = "waiting" if state == "retry_scheduled" else "blocked"
        _add_item(
            items,
            dedupe_key=f"external_write:{workflow_key}:{step_id}:{connector_key}:{state}",
            category="external_write",
            item_type="external_write_failure",
            severity=severity,
            status=status,
            title=f"External marketing write is {state.replace('_', ' ')}",
            message=str(row.get("reason") or row.get("message") or "External write cannot be treated as complete."),
            owner_role=_string_or_none(row.get("owner_role")) or "marketing_ops",
            due_at=_string_or_none(row.get("due_at") or row.get("resume_at")),
            action_key=str(row.get("next_action") or row.get("next_action_cta") or "resolve_external_write_failure"),
            affected_workflow=workflow_key,
            affected_connector=connector_key,
            source_refs=[{"type": "external_write", "state": state, "workflow_id": workflow_key, "step_id": step_id}],
            audit_refs=_audit_refs(row),
            created_at=_string_or_none(row.get("created_at") or row.get("attempted_at")),
            now=now,
            customer_facing=True,
        )
        for escalation in _dicts([row.get("escalation_decision")]):
            _add_item(
                items,
                dedupe_key=f"escalation:{row.get('audit_reference') or _stable_key('write', row)}",
                category="escalation",
                item_type="external_write_escalation",
                severity=_severity(escalation.get("severity"), default="high"),
                status="open",
                title="External-write failure escalation",
                message=str(escalation.get("reason") or "External write failure needs escalation handling."),
                owner_role=_string_or_none(escalation.get("escalation_target")) or "cmo",
                due_at=_string_or_none(escalation.get("due_at")),
                action_key=str(escalation.get("next_action_cta") or "review_escalation"),
                affected_workflow=workflow_key,
                affected_connector=connector_key,
                source_refs=[{"type": "external_write_escalation", "state": state}],
                audit_refs=_audit_refs(escalation),
                now=now,
                customer_facing=True,
            )


def _collect_kpi_items(
    items: dict[str, dict[str, Any]],
    kpi_results: Iterable[Mapping[str, Any]],
    now: datetime,
) -> None:
    for row in _dicts(kpi_results):
        status = _normalize_key(row.get("status"))
        if status not in BAD_KPI_STATES:
            continue
        key = _string_or_none(row.get("kpi_key")) or "unknown_kpi"
        severity = "high" if status in {"blocked", "unavailable"} else "medium"
        message = _first_text(row.get("blocked_reasons") or row.get("missing_requirements"))
        if not message:
            message = f"KPI status is {status}; confidence {row.get('confidence', 'unknown')}."
        _add_item(
            items,
            dedupe_key=f"kpi:{key}",
            category="kpi",
            item_type="kpi_readiness",
            severity=severity,
            status="blocked" if severity == "high" else "open",
            title=f"KPI needs attention: {key.replace('_', ' ')}",
            message=message,
            owner_role=_string_or_none(row.get("owner_role")) or "marketing_ops",
            action_key=str(row.get("next_action_cta") or "review_kpi_readiness"),
            affected_kpi=key,
            source_refs=_source_refs(row, default_type="kpi_result"),
            audit_refs=_audit_refs(row),
            created_at=_string_or_none(row.get("last_computed_at")),
            now=now,
        )


def _collect_reconciliation_items(
    items: dict[str, dict[str, Any]],
    reconciliation_checks: Iterable[Mapping[str, Any]],
    now: datetime,
) -> None:
    for row in _dicts(reconciliation_checks):
        status = _normalize_key(row.get("status"))
        if status not in BAD_RECONCILIATION_STATES:
            continue
        key = _string_or_none(row.get("reconciliation_key")) or "unknown_reconciliation"
        severity = _reconciliation_severity(row, status)
        _add_item(
            items,
            dedupe_key=f"reconciliation:{key}",
            category="reconciliation",
            item_type="reconciliation_issue",
            severity=severity,
            status="blocked" if severity in {"critical", "high"} else "open",
            title=f"Reconciliation needs attention: {key.replace('_', ' ')}",
            message=str(row.get("message") or row.get("reason") or f"Reconciliation status is {status}."),
            owner_role="marketing_ops",
            action_key=str(row.get("next_action_cta") or "resolve_reconciliation"),
            affected_kpi=", ".join(_string_list(row.get("affected_kpi_keys"))) or None,
            source_refs=_source_refs(row, default_type="reconciliation_check"),
            audit_refs=_audit_refs(row),
            now=now,
        )


def _collect_report_quality_items(
    items: dict[str, dict[str, Any]],
    report_quality_gates: Iterable[Mapping[str, Any]],
    now: datetime,
) -> None:
    for row in _dicts(report_quality_gates):
        status = _normalize_key(row.get("status"))
        safe_mode = _normalize_key(row.get("safe_report_mode"))
        if status not in BAD_REPORT_STATES and safe_mode in {"", "deliverable"}:
            continue
        key = _string_or_none(row.get("report_key") or row.get("report_type")) or "unknown_report"
        if status in {"blocked", "unavailable"}:
            severity = _severity(row.get("severity"), default="high")
            if severity in {"low", "info", "medium"}:
                severity = "high"
        else:
            severity = "medium" if safe_mode in {"internal_only", "draft_only"} else "low"
        _add_item(
            items,
            dedupe_key=f"report:{key}",
            category="report",
            item_type="report_quality_gate",
            severity=severity,
            status="blocked" if status in {"blocked", "unavailable"} else "open",
            title=f"Report quality gate needs attention: {row.get('display_name') or key}",
            message=_first_text(row.get("blocked_reasons") or row.get("warning_reasons"))
            or f"Report gate status is {status}; safe mode is {safe_mode or 'unknown'}.",
            owner_role="marketing_ops",
            action_key=str(row.get("next_action_cta") or "review_report_quality"),
            affected_report=key,
            affected_kpi=", ".join(_string_list(row.get("blocked_kpi_keys") or row.get("degraded_kpi_keys"))) or None,
            source_refs=_source_refs(row, default_type="report_quality_gate"),
            audit_refs=_audit_refs(row),
            created_at=_string_or_none(row.get("evaluated_at")),
            now=now,
        )


def _collect_source_context_items(
    items: dict[str, dict[str, Any]],
    source: Mapping[str, Any],
    now: datetime,
) -> None:
    if source.get("production_data_blocked"):
        _add_item(
            items,
            dedupe_key="source:production_data_blocked",
            category="source_data",
            item_type="production_data_blocked",
            severity="high",
            status="blocked",
            title="Production CMO data is blocked",
            message=str(
                source.get("message")
                or "Real connector, mapping, and backfill evidence is required before production KPI/report readiness."
            ),
            owner_role="marketing_ops",
            action_key="connect_real_kpi_sources",
            source_refs=[{"type": "source_context", "production_data_blocked": True}],
            now=now,
        )
    elif source.get("demo") or source.get("demo_suppressed"):
        _add_item(
            items,
            dedupe_key="source:demo_data",
            category="source_data",
            item_type="demo_data",
            severity="medium",
            status="open",
            title="CMO dashboard is using demo/sample data",
            message="Demo/sample values are not production readiness proof for CMO workflows or reports.",
            owner_role="marketing_ops",
            action_key="connect_real_kpi_sources",
            source_refs=[{"type": "source_context", "demo": bool(source.get("demo"))}],
            now=now,
        )


def _collect_crisis_risk_items(
    items: dict[str, dict[str, Any]],
    source: Mapping[str, Any],
    now: datetime,
) -> None:
    risks = _dicts(source.get("crisis_risks") or source.get("brand_crisis_risks"))
    single = source.get("crisis_risk") or source.get("brand_crisis_risk")
    risks.extend(_dicts([single]))
    for risk in risks:
        if not risk:
            continue
        severity = _severity(
            risk.get("severity"),
            default="critical" if risk.get("public_response_required") else "high",
        )
        risk_id = _string_or_none(risk.get("risk_id") or risk.get("id")) or _stable_key("crisis", risk)
        _add_item(
            items,
            dedupe_key=f"crisis:{risk_id}",
            category="crisis_risk",
            item_type="crisis_public_response_risk",
            severity=severity,
            status="blocked" if risk.get("public_response_required") else "open",
            title=str(risk.get("title") or "Brand/crisis response risk"),
            message=str(risk.get("message") or risk.get("reason") or "Crisis/public-response risk needs CMO review."),
            owner_role=_string_or_none(risk.get("owner_role")) or "cmo",
            due_at=_string_or_none(risk.get("due_at")),
            action_key=str(risk.get("next_action_cta") or "open_incident_room"),
            affected_workflow="brand_crisis_response",
            affected_capability="brand_crisis_response",
            source_refs=_source_refs(risk, default_type="crisis_risk"),
            audit_refs=_audit_refs(risk),
            created_at=_string_or_none(risk.get("created_at")),
            now=now,
            customer_facing=True,
        )


def _add_policy_item(
    items: dict[str, dict[str, Any]],
    *,
    dedupe_key: str,
    title: str,
    message: str,
    action_key: str,
    workflow_key: str | None,
    audit_refs: Iterable[Any] = (),
    now: datetime,
) -> None:
    _add_item(
        items,
        dedupe_key=dedupe_key,
        category="policy",
        item_type="policy_gap",
        severity="high",
        status="blocked",
        title=title,
        message=message,
        owner_role="policy_owner",
        action_key=action_key,
        affected_workflow=workflow_key,
        source_refs=[{"type": "marketing_policy", "workflow_key": workflow_key}],
        audit_refs=audit_refs,
        now=now,
        customer_facing=True,
    )


def _add_audit_item(
    items: dict[str, dict[str, Any]],
    *,
    dedupe_key: str,
    title: str,
    message: str,
    action_key: str,
    workflow_key: str | None,
    audit_refs: Iterable[Any] = (),
    now: datetime,
) -> None:
    _add_item(
        items,
        dedupe_key=dedupe_key,
        category="audit",
        item_type="audit_gap",
        severity="high",
        status="blocked",
        title=title,
        message=message,
        owner_role="marketing_ops",
        action_key=action_key,
        affected_workflow=workflow_key,
        source_refs=[{"type": "decision_audit", "workflow_key": workflow_key}],
        audit_refs=audit_refs,
        now=now,
        customer_facing=True,
    )


def _add_item(
    items: dict[str, dict[str, Any]],
    *,
    dedupe_key: str,
    category: str,
    item_type: str,
    severity: str,
    status: str,
    title: str,
    message: str,
    owner_role: str,
    action_key: str,
    now: datetime,
    affected_workflow: str | None = None,
    affected_capability: str | None = None,
    affected_kpi: str | None = None,
    affected_report: str | None = None,
    affected_connector: str | None = None,
    due_at: str | None = None,
    source_refs: Iterable[Any] = (),
    audit_refs: Iterable[Any] = (),
    created_at: str | None = None,
    customer_facing: bool = False,
) -> None:
    severity = _severity(severity)
    status = status if status in ITEM_STATUSES else "open"
    cta = _cta(action_key)
    normalized_key = _normalize_key(dedupe_key)
    priority = _priority_score(
        severity=severity,
        category=category,
        status=status,
        due_at=due_at,
        customer_facing=customer_facing,
        now=now,
    )
    item = {
        "item_id": _item_id(normalized_key),
        "type": item_type,
        "category": category,
        "severity": severity,
        "priority_score": priority,
        "title": title,
        "message": message,
        "affected_workflow": affected_workflow,
        "affected_capability": affected_capability,
        "affected_kpi": affected_kpi,
        "affected_report": affected_report,
        "affected_connector": affected_connector,
        "owner_role": owner_role,
        "due_at": due_at,
        "source_refs": _refs(source_refs),
        "audit_refs": _strings(audit_refs),
        "next_action_cta": cta,
        "next_action_label": cta["label"],
        "next_action_path": cta["path"],
        "next_action_key": cta["action_key"],
        "status": status,
        "created_at": created_at or now.isoformat(),
        "updated_at": now.isoformat(),
    }
    existing = items.get(normalized_key)
    if existing is None:
        items[normalized_key] = item
        return

    if item["priority_score"] > existing["priority_score"]:
        for key in (
            "type",
            "category",
            "severity",
            "priority_score",
            "title",
            "owner_role",
            "due_at",
            "next_action_cta",
            "next_action_label",
            "next_action_path",
            "next_action_key",
            "status",
        ):
            existing[key] = item[key]
    if item["message"] and item["message"] not in existing["message"]:
        existing["message"] = f"{existing['message']} {item['message']}"
    for field in (
        "affected_workflow",
        "affected_capability",
        "affected_kpi",
        "affected_report",
        "affected_connector",
    ):
        existing[field] = existing.get(field) or item.get(field)
    existing["source_refs"] = _unique_refs([*existing["source_refs"], *item["source_refs"]])
    existing["audit_refs"] = _unique_strings([*existing["audit_refs"], *item["audit_refs"]])
    existing["updated_at"] = now.isoformat()


def _sort_items(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [dict(item) for item in items],
        key=lambda item: (
            -int(item.get("priority_score") or 0),
            _due_sort_key(item.get("due_at")),
            str(item.get("title") or ""),
        ),
    )


def _priority_score(
    *,
    severity: str,
    category: str,
    status: str,
    due_at: str | None,
    customer_facing: bool,
    now: datetime,
) -> int:
    score = SEVERITY_SCORE[severity] + CATEGORY_SCORE.get(category, 0)
    if status == "blocked":
        score += 40
    elif status == "waiting":
        score += 20
    if customer_facing:
        score += 60
    due = _parse_datetime(due_at)
    if due is not None:
        if due <= now:
            score += 80
        elif due <= now + timedelta(hours=4):
            score += 45
        elif due <= now + timedelta(days=1):
            score += 20
    return int(score)


def _connector_severity(state: str, write_related: bool = False) -> str:
    normalized = _normalize_key(state)
    if normalized in {"expired_auth", "auth_expired", "connector_disabled", "malformed_payload"}:
        return "high"
    if normalized in {"missing", "unconfigured", "insufficient_scope", "missing_scope"}:
        return "high" if write_related else "medium"
    if normalized in {"timeout", "vendor_5xx", "quota_exhausted"}:
        return "high" if write_related else "medium"
    if normalized in {"stale", "stale_data", "degraded", "partial", "partial_data", "rate_limited"}:
        return "medium"
    return "low"


def _connector_action(state: str) -> str:
    normalized = _normalize_key(state)
    if normalized in {"expired_auth", "auth_expired"}:
        return "reconnect"
    if normalized in {"missing", "unconfigured"}:
        return "setup"
    if normalized in {"insufficient_scope", "missing_scope"}:
        return "add_scope"
    if normalized in {"stale", "stale_data"}:
        return "refresh"
    if normalized in {"timeout", "vendor_5xx", "rate_limited"}:
        return "review_retry_budget"
    if normalized == "connector_disabled":
        return "enable_connector"
    if normalized == "malformed_payload":
        return "fix_connector_payload"
    return "review"


def _reconciliation_severity(row: Mapping[str, Any], status: str) -> str:
    explicit = _severity(row.get("severity"), default="")
    if explicit:
        return explicit
    if status in {"failed", "blocked"}:
        return "high"
    if status == "warning":
        return "medium"
    return "low"


def _severity(value: Any, *, default: str = "medium") -> str:
    normalized = _normalize_key(value)
    if normalized in SEVERITIES:
        return normalized
    return default


def _cta(action_key: Any) -> dict[str, str]:
    key = _normalize_key(action_key) or "none"
    return {
        "action_key": key,
        "label": CTA_LABELS.get(key, key.replace("_", " ").title()),
        "path": CTA_PATHS.get(key, "/dashboard/cmo"),
    }


CTA_LABELS = {
    "none": "No Action",
    "setup": "Set Up",
    "reconnect": "Reconnect",
    "add_scope": "Add Scope",
    "refresh": "Refresh Sync",
    "review": "Review",
    "review_retry_budget": "Review Retry Budget",
    "review_degraded": "Review Degraded",
    "configure_idempotency": "Configure Idempotency",
    "fix_connector_payload": "Fix Connector Payload",
    "wait_for_quota_reset": "Wait For Quota Reset",
    "enable_connector": "Enable Connector",
    "map_fields": "Map Fields",
    "complete_mapping": "Complete Mapping",
    "fix_required_mapping": "Fix Mapping",
    "complete_backfill": "Complete Backfill",
    "retry_backfill": "Retry Backfill",
    "resolve_blocker": "Resolve Blocker",
    "resolve_overdue_approvals": "Resolve Approval",
    "review_pending_approvals": "Review Approval",
    "review_escalated_approval": "Review Escalation",
    "configure_escalation_matrix": "Configure Escalation",
    "review_escalation": "Review Escalation",
    "configure_marketing_policy_manifest": "Configure Policy",
    "configure_decision_audit_package": "Configure Audit",
    "resolve_marketing_policy": "Resolve Policy",
    "resolve_external_write_failure": "Resolve Write Failure",
    "manual_reconcile_before_retry": "Reconcile Before Retry",
    "wait_for_idempotent_retry": "Wait For Retry",
    "confirm_write_or_reconcile": "Confirm Or Reconcile",
    "restore_required_kpis": "Restore KPI",
    "review_kpi_readiness": "Review KPI",
    "resolve_reconciliation": "Resolve Reconciliation",
    "resolve_report_reconciliation": "Resolve Reconciliation",
    "review_report_quality": "Review Report",
    "review_report_quality_warnings": "Review Report Warnings",
    "fix_report_connectors": "Fix Report Connectors",
    "refresh_report_sources": "Refresh Report Sources",
    "connect_real_kpi_sources": "Connect Real Sources",
    "activate_report_workflow": "Activate Workflow",
    "promote_workflow": "Promote",
    "resolve_promotion_blocker": "Resolve Blocker",
    "fix_required_connector": "Fix Connector",
    "run_shadow_quality": "Run Shadow QA",
    "review_degraded_dependency": "Review Degraded",
    "resume_workflow": "Resume",
    "implement_first_class_agent": "Implement Agent",
    "open_incident_room": "Open Incident Room",
}

CTA_PATHS = {
    "none": "/dashboard/cmo",
    "setup": "/dashboard/connectors",
    "reconnect": "/dashboard/connectors",
    "add_scope": "/dashboard/connectors",
    "refresh": "/dashboard/connectors",
    "review": "/dashboard/connectors",
    "review_retry_budget": "/dashboard/connectors",
    "review_degraded": "/dashboard/connectors",
    "configure_idempotency": "/dashboard/connectors",
    "fix_connector_payload": "/dashboard/connectors",
    "wait_for_quota_reset": "/dashboard/connectors",
    "enable_connector": "/dashboard/connectors",
    "map_fields": "/dashboard/cmo?panel=data-readiness",
    "complete_mapping": "/dashboard/cmo?panel=data-readiness",
    "fix_required_mapping": "/dashboard/cmo?panel=data-readiness",
    "complete_backfill": "/dashboard/cmo?panel=data-readiness",
    "retry_backfill": "/dashboard/cmo?panel=data-readiness",
    "resolve_blocker": "/dashboard/cmo?panel=data-readiness",
    "resolve_overdue_approvals": "/dashboard/approvals",
    "review_pending_approvals": "/dashboard/approvals",
    "review_escalated_approval": "/dashboard/approvals",
    "configure_escalation_matrix": "/dashboard/settings",
    "review_escalation": "/dashboard/approvals",
    "configure_marketing_policy_manifest": "/dashboard/settings",
    "configure_decision_audit_package": "/dashboard/audit",
    "resolve_marketing_policy": "/dashboard/settings",
    "resolve_external_write_failure": "/dashboard/approvals",
    "manual_reconcile_before_retry": "/dashboard/approvals",
    "wait_for_idempotent_retry": "/dashboard/approvals",
    "confirm_write_or_reconcile": "/dashboard/approvals",
    "restore_required_kpis": "/dashboard/cmo?kpi=all",
    "review_kpi_readiness": "/dashboard/cmo?kpi=all",
    "resolve_reconciliation": "/dashboard/cmo?panel=reconciliation",
    "resolve_report_reconciliation": "/dashboard/cmo?panel=reconciliation",
    "review_report_quality": "/dashboard/reports",
    "review_report_quality_warnings": "/dashboard/reports",
    "fix_report_connectors": "/dashboard/connectors",
    "refresh_report_sources": "/dashboard/connectors",
    "connect_real_kpi_sources": "/dashboard/connectors",
    "activate_report_workflow": "/dashboard/workflows",
    "promote_workflow": "/dashboard/workflows",
    "resolve_promotion_blocker": "/dashboard/workflows",
    "fix_required_connector": "/dashboard/connectors",
    "run_shadow_quality": "/dashboard/workflows",
    "review_degraded_dependency": "/dashboard/workflows",
    "resume_workflow": "/dashboard/workflows",
    "implement_first_class_agent": "/dashboard/agents",
    "open_incident_room": "/dashboard/approvals",
}


def _external_write_results_from_source(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in (
        "external_write_results",
        "marketing_external_write_results",
        "external_write_failures",
        "marketing_external_write_failures",
    ):
        rows.extend(_dicts(source.get(key)))
    return rows


def _connector_setup_ref(row: Mapping[str, Any], state: str) -> dict[str, Any]:
    return {
        "type": "connector_setup",
        "connector_key": row.get("key") or row.get("connector_key"),
        "category": row.get("category"),
        "status": row.get("configured_status"),
        "health_status": row.get("health_status"),
        "state": state,
        "last_sync_at": row.get("last_sync_at"),
    }


def _connector_contract_ref(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "connector_contract",
        "connector_key": row.get("connector_key"),
        "category": row.get("category"),
        "contract_state": row.get("contract_state"),
        "read_status": row.get("read_status"),
        "write_status": row.get("write_status"),
        "failure_class": row.get("failure_class"),
    }


def _approval_ref(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "approval",
        "approval_id": row.get("approval_id"),
        "approval_review_id": row.get("approval_review_id"),
        "approval_review_status": row.get("approval_review_status"),
        "approval_type": row.get("approval_type"),
        "workflow_id": row.get("workflow_id"),
        "step_id": row.get("step_id"),
    }


def _source_refs(row: Mapping[str, Any], *, default_type: str) -> list[dict[str, Any]]:
    refs = []
    for key in ("source_refs", "source_references", "sources_compared", "sources"):
        for value in _list_from_value(row.get(key)):
            if isinstance(value, Mapping):
                refs.append(dict(value))
            else:
                refs.append({"type": default_type, "ref": str(value)})
    if not refs:
        refs.append({"type": default_type})
    return _unique_refs(refs)


def _audit_refs(row: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(row, Mapping):
        return []
    refs: list[Any] = [
        row.get("audit_ref"),
        row.get("audit_reference"),
        row.get("decision_audit_ref"),
    ]
    for key in ("audit_refs", "decision_audit_refs"):
        refs.extend(_list_from_value(row.get(key)))
    evidence = row.get("audit_evidence")
    if isinstance(evidence, Mapping):
        refs.extend([evidence.get("audit_reference"), evidence.get("decision_audit_ref")])
    for event in _dicts(row.get("audit_events")):
        refs.extend([event.get("audit_reference"), event.get("decision_audit_ref")])
    return _unique_strings(_strings(refs))


def _refs(values: Iterable[Any]) -> list[dict[str, Any]]:
    refs = []
    for value in values:
        if isinstance(value, Mapping):
            refs.append(dict(value))
        elif value not in {None, ""}:
            refs.append({"ref": str(value)})
    return _unique_refs(refs)


def _unique_refs(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for value in values:
        encoded = repr(sorted(dict(value).items()))
        if encoded in seen:
            continue
        seen.add(encoded)
        result.append(dict(value))
    return result


def _strings(values: Iterable[Any]) -> list[str]:
    return [str(value) for value in values if _string_or_none(value)]


def _unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, str) or value is None:
        return []
    if isinstance(value, Iterable):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _list_from_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in _list_from_value(value) if _string_or_none(item)]


def _first_text(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for nested in value.values():
            text = _first_text(nested)
            if text:
                return text
        return None
    for item in _list_from_value(value):
        if isinstance(item, Mapping):
            text = _first_text(item)
            if text:
                return text
        elif _string_or_none(item):
            return str(item)
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if isinstance(value, str) and value.strip():
        try:
            return _ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _ensure_aware(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _due_sort_key(value: Any) -> str:
    due = _parse_datetime(value)
    return due.isoformat() if due else "9999-12-31T23:59:59+00:00"


def _item_id(dedupe_key: str) -> str:
    return f"cmo_wq_{hashlib.sha256(dedupe_key.encode()).hexdigest()[:20]}"


def _stable_key(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}_{hashlib.sha256(repr(sorted(value.items())).encode()).hexdigest()[:16]}"


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
