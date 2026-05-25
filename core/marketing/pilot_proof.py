"""CMO pilot tenant proof projection.

CMO-9.4 does not make live vendor claims by itself. It packages evidence from
existing CMO readiness, governance, KPI, report, workbench, approval, agent
contract, scenario, and chaos projections so a pilot tenant can be evaluated
without treating demo data, mocks, stubs, or test doubles as production proof.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from core.marketing.agent_contracts import marketing_agent_contract_specs

CMO_PILOT_PROOF_VERSION = "2026-05-24.cmo-9.4"

ENVIRONMENT_TYPES = {"real_vendor", "vendor_sandbox", "demo", "test_double", "unknown"}
PROOF_STATUSES = {"passed", "partial", "blocked", "demo_only", "test_only", "unavailable"}
DEMO_SOURCE_MARKERS = {"demo", "sample", "mock", "stub", "hardcoded", "fallback"}
TEST_SOURCE_MARKERS = {"test", "test_double", "fake", "fixture", "contract_test_double"}
SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)

FIRST_CLASS_UNPROVEN_AGENT_KEYS: dict[str, str] = {}


def build_cmo_pilot_proof_projection(
    *,
    tenant_id: str | None = None,
    company_id: str | None = None,
    environment_type: str | None = None,
    source_context: Mapping[str, Any] | None = None,
    connector_setup: Iterable[Mapping[str, Any]] | None = None,
    connector_contracts: Iterable[Mapping[str, Any]] | None = None,
    data_readiness: Mapping[str, Any] | None = None,
    workflow_activation: Mapping[str, Any] | None = None,
    workflow_lint_results: Iterable[Any] | None = None,
    policy_projection: Mapping[str, Any] | None = None,
    escalation_projection: Mapping[str, Any] | None = None,
    approval_timeout_risk: Mapping[str, Any] | None = None,
    external_write_results: Iterable[Mapping[str, Any]] | None = None,
    decision_audit_projection: Mapping[str, Any] | None = None,
    kpi_schema: Iterable[Mapping[str, Any]] | None = None,
    kpi_results: Iterable[Mapping[str, Any]] | None = None,
    reconciliation_checks: Iterable[Mapping[str, Any]] | None = None,
    report_quality_gates: Iterable[Mapping[str, Any]] | None = None,
    work_queue: Iterable[Mapping[str, Any]] | None = None,
    kpi_drilldowns: Iterable[Mapping[str, Any]] | None = None,
    approval_review_projection: Mapping[str, Any] | None = None,
    agent_contracts: Iterable[Mapping[str, Any]] | None = None,
    scenario_evidence: Iterable[Mapping[str, Any]] | None = None,
    chaos_evidence: Iterable[Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a deterministic pilot proof package and compact summary."""

    generated_at = _iso(now or datetime.now(UTC))
    source = source_context if isinstance(source_context, Mapping) else {}
    setup_rows = _dicts(connector_setup)
    contract_rows = _dicts(connector_contracts)
    data = data_readiness if isinstance(data_readiness, Mapping) else {}
    workflows = workflow_activation if isinstance(workflow_activation, Mapping) else {}
    policy = policy_projection if isinstance(policy_projection, Mapping) else {}
    escalation = escalation_projection if isinstance(escalation_projection, Mapping) else {}
    approval_timeout = approval_timeout_risk if isinstance(approval_timeout_risk, Mapping) else {}
    audit = decision_audit_projection if isinstance(decision_audit_projection, Mapping) else {}
    approvals = approval_review_projection if isinstance(approval_review_projection, Mapping) else {}
    kpi_defs = _dicts(kpi_schema)
    kpis = _dicts(kpi_results)
    reconciliations = _dicts(reconciliation_checks)
    reports = _dicts(report_quality_gates)
    queue = _dicts(work_queue)
    drilldowns = _dicts(kpi_drilldowns)
    writes = _dicts(external_write_results)
    scenarios = _dicts(scenario_evidence)
    chaos = _dicts(chaos_evidence)
    agents = _dicts(agent_contracts) or marketing_agent_contract_specs()

    env = _resolve_environment_type(environment_type, source, contract_rows)
    blockers: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    proven: list[dict[str, Any]] = []
    unproven: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []
    test_evidence_refs: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []

    _evaluate_environment(env, source, contract_rows, blockers, risks, next_actions)
    _evaluate_connector_setup(setup_rows, blockers, proven, evidence_refs, next_actions)
    _evaluate_connector_contracts(contract_rows, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_data_readiness(data, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_workflow_activation(workflows, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_workflow_lint(workflow_lint_results, blockers, risks, proven, test_evidence_refs, next_actions)
    _evaluate_summary_projection(
        policy,
        summary_key="marketing_policy_summary",
        capability_key="marketing_policy_manifest",
        blocker_status="missing_policy",
        blocker_category="policy",
        blocker_message="Marketing policy manifest is missing or not ready.",
        blockers=blockers,
        proven=proven,
        evidence_refs=evidence_refs,
        next_actions=next_actions,
    )
    _evaluate_summary_projection(
        escalation,
        summary_key="marketing_escalation_summary",
        capability_key="marketing_escalation_matrix",
        blocker_status="missing_route",
        blocker_category="escalation",
        blocker_message="Marketing escalation matrix has no route for required pilot events.",
        blockers=blockers,
        proven=proven,
        evidence_refs=evidence_refs,
        next_actions=next_actions,
    )
    _evaluate_approval_timeout(approval_timeout, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_external_writes(writes, contract_rows, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_summary_projection(
        audit,
        summary_key="marketing_decision_audit_summary",
        capability_key="decision_audit_package",
        blocker_status="missing_audit_evidence",
        blocker_category="audit",
        blocker_message="CMO decision audit package or required evidence is missing.",
        blockers=blockers,
        proven=proven,
        evidence_refs=evidence_refs,
        next_actions=next_actions,
    )
    _evaluate_kpis(kpi_defs, kpis, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_reconciliation(reconciliations, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_report_gates(reports, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_work_queue(queue, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_drilldowns(drilldowns, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_approval_reviews(approvals, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_agent_contracts(agents, proven, unproven, evidence_refs)
    _evaluate_test_evidence("scenario", scenarios, risks, proven, test_evidence_refs)
    _evaluate_test_evidence("chaos", chaos, risks, proven, test_evidence_refs)

    source_refs = _connector_source_refs(contract_rows, drilldowns, kpis)
    report_refs = _report_refs(reports)
    audit_refs = _audit_refs(audit, approvals, writes, reports, kpis)
    if not source_refs:
        _add_issue(
            risks if env == "vendor_sandbox" else blockers,
            category="source_lineage",
            message="Pilot proof has no real connector/source lineage refs.",
            severity="medium" if env == "vendor_sandbox" else "high",
            next_action="connect_real_or_sandbox_sources",
        )
        next_actions.append(_next_action("connect_real_or_sandbox_sources", "Connect Real/Sandbox Sources"))

    _apply_environment_specific_rules(env, blockers, risks, proven, source_refs, report_refs, audit_refs, next_actions)
    blockers = _dedupe_issues(blockers)
    risks = _dedupe_issues(risks)
    proven = _dedupe_capabilities(proven)
    unproven = _dedupe_capabilities(unproven)
    evidence_refs = _dedupe_refs(evidence_refs)
    test_evidence_refs = _dedupe_refs(test_evidence_refs)
    next_actions = _dedupe_actions(next_actions)

    status = _proof_status(env, blockers, risks)
    score = _readiness_score(env, blockers, risks, proven)
    proof: dict[str, Any] = {
        "proof_id": _proof_id(tenant_id, company_id, env, blockers, risks, proven, source_refs, report_refs),
        "schema_version": CMO_PILOT_PROOF_VERSION,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "environment_type": env,
        "proof_scope": "sandbox" if env == "vendor_sandbox" else env,
        "proof_status": status,
        "production_claim_allowed": env == "real_vendor" and status == "passed",
        "real_vendor_claim_allowed": env == "real_vendor" and status == "passed",
        "full_cmo_autonomy_claim_allowed": False,
        "readiness_score": score,
        "proven_capabilities": proven,
        "unproven_capabilities": unproven,
        "blockers": blockers,
        "risks": risks,
        "evidence_refs": evidence_refs,
        "test_evidence_refs": test_evidence_refs,
        "connector_source_refs": source_refs,
        "report_proof_refs": report_refs,
        "audit_refs": audit_refs,
        "next_actions": next_actions,
        "generated_at": generated_at,
    }
    return {
        "cmo_pilot_proof_version": CMO_PILOT_PROOF_VERSION,
        "cmo_pilot_proof": _redact(proof),
        "cmo_pilot_proof_summary": summarize_cmo_pilot_proof(proof),
        "cmo_pilot_evidence_bundle": build_cmo_pilot_evidence_bundle(proof),
    }


def build_cmo_pilot_proof_projection_from_kpi_payload(
    payload: Mapping[str, Any] | None,
    *,
    environment_type: str | None = None,
    scenario_evidence: Iterable[Mapping[str, Any]] | None = None,
    chaos_evidence: Iterable[Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build pilot proof from an existing `/kpis/cmo` response payload."""

    data = payload if isinstance(payload, Mapping) else {}
    return build_cmo_pilot_proof_projection(
        tenant_id=_string_or_none(data.get("tenant_id")),
        company_id=_string_or_none(data.get("company_id")),
        environment_type=environment_type,
        source_context=data,
        connector_setup=_dicts(data.get("connector_setup")),
        connector_contracts=_dicts(data.get("connector_contracts")),
        data_readiness={
            "field_mapping_status": _dicts(data.get("field_mapping_status")),
            "backfill_status": _dicts(data.get("backfill_status")),
            "kpi_readiness": data.get("kpi_readiness") or {},
        },
        workflow_activation={
            "workflow_activation_status": _dicts(data.get("workflow_activation_status")),
            "workflow_activation_summary": data.get("workflow_activation_summary") or {},
        },
        workflow_lint_results=data.get("workflow_lint_results") or data.get("marketing_workflow_lint_results") or [],
        policy_projection={
            "marketing_policy_manifest": data.get("marketing_policy_manifest"),
            "marketing_policy_summary": data.get("marketing_policy_summary") or {},
        },
        escalation_projection={
            "marketing_escalation_matrix": data.get("marketing_escalation_matrix"),
            "marketing_escalation_summary": data.get("marketing_escalation_summary") or {},
        },
        approval_timeout_risk=data.get("approval_timeout_risk") or {},
        external_write_results=data.get("external_write_results") or data.get("marketing_external_write_results") or [],
        decision_audit_projection={
            "marketing_decision_audit": data.get("marketing_decision_audit"),
            "marketing_decision_audit_summary": data.get("marketing_decision_audit_summary") or {},
        },
        kpi_schema=_dicts(data.get("unified_cmo_kpi_schema")),
        kpi_results=_dicts(data.get("unified_cmo_kpi_results")),
        reconciliation_checks=_dicts(data.get("cmo_kpi_reconciliation_checks")),
        report_quality_gates=_dicts(data.get("report_quality_gates")),
        work_queue=_dicts(data.get("cmo_work_queue")),
        kpi_drilldowns=_dicts(data.get("cmo_kpi_drilldowns")),
        approval_review_projection={
            "cmo_approval_reviews": _dicts(data.get("cmo_approval_reviews")),
            "cmo_approval_review_summary": data.get("cmo_approval_review_summary") or {},
        },
        agent_contracts=data.get("marketing_agent_contracts") or data.get("agent_contracts") or [],
        scenario_evidence=scenario_evidence or data.get("cmo_scenario_evidence") or [],
        chaos_evidence=chaos_evidence or data.get("cmo_chaos_evidence") or [],
        now=now,
    )


def summarize_cmo_pilot_proof(proof: Mapping[str, Any]) -> dict[str, Any]:
    status = str(proof.get("proof_status") or "unavailable")
    blockers = _dicts(proof.get("blockers"))
    risks = _dicts(proof.get("risks"))
    return {
        "schema_version": CMO_PILOT_PROOF_VERSION,
        "proof_id": proof.get("proof_id"),
        "environment_type": proof.get("environment_type") or "unknown",
        "proof_status": status,
        "readiness_score": proof.get("readiness_score") or 0,
        "production_claim_allowed": bool(proof.get("production_claim_allowed")),
        "real_vendor_claim_allowed": bool(proof.get("real_vendor_claim_allowed")),
        "full_cmo_autonomy_claim_allowed": False,
        "proven_capabilities": len(_dicts(proof.get("proven_capabilities"))),
        "unproven_capabilities": len(_dicts(proof.get("unproven_capabilities"))),
        "blockers": len(blockers),
        "risks": len(risks),
        "next_action_cta": _summary_next_action(status, blockers, risks),
    }


def build_cmo_pilot_evidence_bundle(proof: Mapping[str, Any]) -> dict[str, Any]:
    """Return a lightweight, secret-redacted evidence bundle for docs/future UI."""

    redacted = _redact(dict(proof))
    return {
        "bundle_version": CMO_PILOT_PROOF_VERSION,
        "bundle_type": "cmo_pilot_proof",
        "proof_id": redacted.get("proof_id"),
        "environment_type": redacted.get("environment_type"),
        "proof_status": redacted.get("proof_status"),
        "generated_at": redacted.get("generated_at"),
        "summary": summarize_cmo_pilot_proof(redacted),
        "evidence": {
            "proven_capabilities": redacted.get("proven_capabilities") or [],
            "unproven_capabilities": redacted.get("unproven_capabilities") or [],
            "blockers": redacted.get("blockers") or [],
            "risks": redacted.get("risks") or [],
            "evidence_refs": redacted.get("evidence_refs") or [],
            "test_evidence_refs": redacted.get("test_evidence_refs") or [],
            "connector_source_refs": redacted.get("connector_source_refs") or [],
            "report_proof_refs": redacted.get("report_proof_refs") or [],
            "audit_refs": redacted.get("audit_refs") or [],
            "next_actions": redacted.get("next_actions") or [],
        },
    }


def serialize_cmo_pilot_evidence_bundle(bundle: Mapping[str, Any]) -> str:
    return _canonical_json(_redact(bundle))


def _evaluate_environment(
    env: str,
    source: Mapping[str, Any],
    contracts: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    if env == "demo":
        _add_issue(
            blockers,
            category="environment",
            message="Demo/sample environments cannot produce production pilot proof.",
            severity="critical",
            next_action="connect_real_or_sandbox_sources",
        )
    elif env == "test_double":
        _add_issue(
            blockers,
            category="environment",
            message="Test-double/mock connector proof cannot produce production pilot proof.",
            severity="critical",
            next_action="replace_test_doubles_with_vendor_sandbox",
        )
    elif env == "unknown":
        _add_issue(
            risks,
            category="environment",
            message="Pilot environment type is unknown; proof cannot be treated as real-vendor evidence.",
            severity="medium",
            next_action="label_pilot_environment",
        )
    if source.get("production_data_blocked"):
        _add_issue(
            blockers,
            category="environment",
            message="Production data policy blocks KPI/report confidence for this tenant.",
            severity="critical",
            next_action="resolve_production_data_blocker",
        )
    if any(row.get("mock_or_test_double") for row in contracts):
        _add_issue(
            blockers,
            category="environment",
            message="At least one connector contract is marked mock/test-double proof.",
            severity="critical",
            next_action="replace_test_doubles_with_vendor_sandbox",
        )
    next_actions.append(_next_action("label_pilot_environment", "Label Pilot Environment"))


def _evaluate_connector_setup(
    rows: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    configured = [row for row in rows if row.get("configured_status") == "configured"]
    if not configured:
        _add_issue(
            blockers,
            category="connector_setup",
            message="No configured marketing connectors are available for pilot proof.",
            severity="critical",
            next_action="connect_marketing_systems",
        )
        next_actions.append(_next_action("connect_marketing_systems", "Connect Marketing Systems"))
        return
    needs_action = [
        row
        for row in rows
        if row.get("configured_status") != "configured"
        or row.get("health_status") in {"unhealthy", "expired_auth", "degraded"}
        or row.get("data_coverage_status") in {"missing", "blocked", "unavailable"}
        or row.get("cta_state") not in {None, "", "none", "connected"}
    ]
    if needs_action:
        _add_issue(
            blockers,
            category="connector_setup",
            message="One or more marketing connectors need setup, reconnect, scope, or health action.",
            severity="high",
            next_action=str(needs_action[0].get("cta_state") or "resolve_connector_setup"),
        )
    else:
        proven.append(_capability("connector_setup", "Configured marketing connectors are healthy."))
    for row in configured:
        refs.append(_ref("connector_setup", row.get("key") or row.get("connector_key"), row))


def _evaluate_connector_contracts(
    rows: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    if not rows:
        _add_issue(
            blockers,
            category="connector_contracts",
            message="Connector contract/read-write readiness evidence is missing.",
            severity="critical",
            next_action="configure_connector_contracts",
        )
        return
    blocked = [
        row
        for row in rows
        if row.get("read_status") == "blocked"
        or row.get("write_status") in {"blocked", "missing_scope", "idempotency_missing"}
        or row.get("blocks_external_writes")
        or row.get("mock_or_test_double")
    ]
    degraded = [row for row in rows if row.get("read_status") == "degraded" or row.get("write_status") == "degraded"]
    if blocked:
        _add_issue(
            blockers,
            category="connector_contracts",
            message="Connector contract evidence blocks read/write production readiness.",
            severity="critical",
            affected=[str(row.get("connector_key")) for row in blocked],
            next_action=str(blocked[0].get("next_action_cta") or "fix_connector_contract"),
        )
        next_actions.append(
            _next_action(
                str(blocked[0].get("next_action_cta") or "fix_connector_contract"),
                "Fix Connector Contract",
            )
        )
    elif degraded:
        _add_issue(
            risks,
            category="connector_contracts",
            message="Connector contract evidence is degraded and lowers pilot confidence.",
            severity="medium",
            affected=[str(row.get("connector_key")) for row in degraded],
            next_action="review_degraded_connectors",
        )
    else:
        proven.append(_capability("connector_contracts", "Connector read/write contracts are ready."))
    for row in rows:
        refs.append(_ref("connector_contract", row.get("connector_key"), row))


def _evaluate_data_readiness(
    data: Mapping[str, Any],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    kpi_readiness = data.get("kpi_readiness") if isinstance(data.get("kpi_readiness"), Mapping) else {}
    status = _normalize(kpi_readiness.get("status") or kpi_readiness.get("readiness"))
    if not data or status in {"", "missing", "blocked"}:
        _add_issue(
            blockers,
            category="data_readiness",
            message="Field mapping or historical backfill readiness blocks pilot proof.",
            severity="critical",
            next_action=str(kpi_readiness.get("next_action_cta") or "resolve_data_readiness"),
        )
        next_actions.append(
            _next_action(
                str(kpi_readiness.get("next_action_cta") or "resolve_data_readiness"),
                "Resolve Data Readiness",
            )
        )
    elif status == "degraded":
        _add_issue(
            risks,
            category="data_readiness",
            message="Field mapping or backfill readiness is degraded.",
            severity="medium",
            next_action=str(kpi_readiness.get("next_action_cta") or "review_data_readiness"),
        )
    else:
        proven.append(_capability("data_mapping_backfill", "Mappings and historical backfills are ready."))
    for row in _dicts(data.get("field_mapping_status")):
        refs.append(_ref("field_mapping", row.get("connector_key"), row))
    for row in _dicts(data.get("backfill_status")):
        refs.append(_ref("backfill", row.get("connector_key"), row))


def _evaluate_workflow_activation(
    projection: Mapping[str, Any],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    rows = _dicts(projection.get("workflow_activation_status"))
    if not rows:
        _add_issue(
            blockers,
            category="workflow_activation",
            message="Workflow activation evidence is missing.",
            severity="high",
            next_action="configure_workflow_activation",
        )
        return
    blocked = [row for row in rows if row.get("state") in {"promotion_blocked", "unavailable"}]
    degraded = [row for row in rows if row.get("state") in {"shadow", "degraded", "paused"}]
    if blocked:
        _add_issue(
            blockers,
            category="workflow_activation",
            message="One or more CMO workflows are blocked or unavailable.",
            severity="high",
            affected=[str(row.get("workflow_key")) for row in blocked],
            next_action=str(blocked[0].get("next_action_cta") or "resolve_workflow_activation"),
        )
    elif degraded:
        _add_issue(
            risks,
            category="workflow_activation",
            message="One or more CMO workflows remain shadow, degraded, or paused.",
            severity="medium",
            affected=[str(row.get("workflow_key")) for row in degraded],
            next_action="review_workflow_modes",
        )
    else:
        proven.append(_capability("workflow_activation", "Pilot workflows have activation evidence."))
    for row in rows:
        refs.append(_ref("workflow_activation", row.get("workflow_key"), row))


def _evaluate_workflow_lint(
    lint_results: Iterable[Any] | None,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    rows = list(lint_results or [])
    if not rows:
        _add_issue(
            risks,
            category="workflow_linter",
            message="Workflow linter evidence is not attached to this pilot proof.",
            severity="medium",
            next_action="attach_workflow_lint_evidence",
        )
        return
    errors = []
    warnings = []
    for row in rows:
        result = _lint_result_dict(row)
        errors.extend(_dicts(result.get("errors")))
        warnings.extend(_dicts(result.get("warnings")))
        refs.append(_ref("workflow_lint", result.get("workflow_file") or result.get("workflow_id"), result))
    if errors:
        _add_issue(
            blockers,
            category="workflow_linter",
            message="Marketing workflow linter has production-blocking errors.",
            severity="high",
            affected=[str(item.get("code")) for item in errors],
            next_action="fix_workflow_lint_errors",
        )
    elif warnings:
        _add_issue(
            risks,
            category="workflow_linter",
            message="Marketing workflow linter has warnings.",
            severity="low",
            affected=[str(item.get("code")) for item in warnings],
            next_action="review_workflow_lint_warnings",
        )
    else:
        proven.append(_capability("workflow_linter", "Workflow lint evidence has no production-blocking errors."))


def _evaluate_summary_projection(
    projection: Mapping[str, Any],
    *,
    summary_key: str,
    capability_key: str,
    blocker_status: str,
    blocker_category: str,
    blocker_message: str,
    blockers: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    summary = projection.get(summary_key) if isinstance(projection, Mapping) else None
    summary_status = _normalize((summary or {}).get("status") or (summary or {}).get("readiness"))
    if not isinstance(summary, Mapping) or summary_status == blocker_status:
        _add_issue(
            blockers,
            category=blocker_category,
            message=blocker_message,
            severity="high",
            next_action=str((summary or {}).get("next_action_cta") or f"configure_{capability_key}"),
        )
        next_actions.append(
            _next_action(
                str((summary or {}).get("next_action_cta") or f"configure_{capability_key}"),
                "Configure Governance",
            )
        )
    else:
        proven.append(_capability(capability_key, f"{capability_key.replace('_', ' ').title()} is ready."))
    if isinstance(summary, Mapping):
        evidence_refs.append(_ref(capability_key, summary.get("policy_id") or summary.get("schema_version"), summary))


def _evaluate_approval_timeout(
    risk: Mapping[str, Any],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    decisions = _dicts(risk.get("approval_timeout_decisions"))
    overdue = _int(risk.get("overdue"))
    timed_out = [
        row
        for row in decisions
        if row.get("timed_out")
        or row.get("outcome") in {"auto_cancel", "require_manual_resolution", "pause_workflow"}
    ]
    if overdue or timed_out:
        _add_issue(
            blockers,
            category="approval_timeout",
            message="Approval timeout evidence contains overdue or fail-closed approvals.",
            severity="high",
            next_action="resolve_approval_timeout",
        )
    else:
        proven.append(_capability("approval_timeout_policy", "Approval timeout state has no overdue blockers."))
    if not risk:
        _add_issue(
            risks,
            category="approval_timeout",
            message="No approval timeout evidence is attached.",
            severity="low",
            next_action="attach_approval_timeout_evidence",
        )
    for row in decisions:
        refs.append(_ref("approval_timeout", row.get("approval_id") or row.get("event_id"), row))


def _evaluate_external_writes(
    writes: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    confirmed = [
        row
        for row in writes
        if row.get("final_state") in {"write_confirmed", "idempotent_recovered"}
        or row.get("step_status") == "completed"
        or row.get("status") in {"write_confirmed", "idempotent_recovered"}
    ]
    confirmed.extend(
        confirmation
        for contract in contracts
        for confirmation in _dicts(contract.get("external_write_confirmations"))
        if confirmation.get("status") in {"write_confirmed", "idempotent_recovered"}
    )
    unsafe = [
        row
        for row in writes
        if row.get("step_status") == "failed"
        or row.get("final_state") in {"rejected", "timeout_unknown", "write_unconfirmed"}
        or row.get("status") in {"rejected", "timeout_unknown", "write_unconfirmed"}
    ]
    if unsafe:
        _add_issue(
            blockers,
            category="external_write",
            message="External write evidence includes rejected, timeout/unknown, failed, or unconfirmed writes.",
            severity="critical",
            next_action="resolve_external_write_failure",
        )
    elif confirmed:
        proven.append(
            _capability(
                "external_write_confirmation",
                "At least one external write is confirmed or idempotently recovered.",
            )
        )
    else:
        _add_issue(
            risks,
            category="external_write",
            message="No confirmed external write evidence is attached; read-only pilot proof can only be partial.",
            severity="medium",
            next_action="attach_confirmed_external_write_evidence",
        )
    for row in writes:
        refs.append(
            _ref(
                "external_write",
                row.get("audit_reference") or row.get("final_state") or row.get("status"),
                row,
            )
        )
    for row in confirmed:
        refs.append(
            _ref(
                "external_write_confirmation",
                row.get("audit_reference") or row.get("external_object_id"),
                row,
            )
        )


def _evaluate_kpis(
    definitions: list[dict[str, Any]],
    results: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    if not definitions or not results:
        _add_issue(
            blockers,
            category="kpi_schema",
            message="Unified CMO KPI schema or KPI result evidence is missing.",
            severity="high",
            next_action="attach_kpi_schema_evidence",
        )
        return
    blocked = [row for row in results if row.get("status") in {"blocked", "unavailable"}]
    degraded = [row for row in results if row.get("status") == "degraded"]
    if blocked:
        _add_issue(
            blockers,
            category="kpi_schema",
            message="One or more critical CMO KPIs are blocked or unavailable.",
            severity="high",
            affected=[str(row.get("kpi_key")) for row in blocked],
            next_action="resolve_kpi_blockers",
        )
    elif degraded:
        _add_issue(
            risks,
            category="kpi_schema",
            message="One or more CMO KPIs are degraded.",
            severity="medium",
            affected=[str(row.get("kpi_key")) for row in degraded],
            next_action="review_degraded_kpis",
        )
    else:
        proven.append(_capability("unified_kpi_schema", "Unified CMO KPI schema/results are ready."))
    for row in results:
        refs.append(_ref("kpi_result", row.get("kpi_key"), row))


def _evaluate_reconciliation(
    checks: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    if not checks:
        _add_issue(
            blockers,
            category="kpi_reconciliation",
            message="KPI reconciliation evidence is missing.",
            severity="high",
            next_action="run_kpi_reconciliation",
        )
        return
    failed = [row for row in checks if row.get("status") in {"failed", "blocked"}]
    warnings = [row for row in checks if row.get("status") == "warning"]
    if failed:
        _add_issue(
            blockers,
            category="kpi_reconciliation",
            message="One or more KPI reconciliation checks failed or are blocked.",
            severity="high",
            affected=[str(row.get("reconciliation_key")) for row in failed],
            next_action="resolve_reconciliation_failure",
        )
    elif warnings:
        _add_issue(
            risks,
            category="kpi_reconciliation",
            message="One or more KPI reconciliation checks warn.",
            severity="medium",
            affected=[str(row.get("reconciliation_key")) for row in warnings],
            next_action="review_reconciliation_warnings",
        )
    else:
        proven.append(_capability("kpi_reconciliation", "KPI reconciliation checks pass."))
    for row in checks:
        refs.append(_ref("kpi_reconciliation", row.get("reconciliation_key"), row))


def _evaluate_report_gates(
    gates: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    if not gates:
        _add_issue(
            blockers,
            category="report_quality",
            message="Report quality gate evidence is missing.",
            severity="high",
            next_action="run_report_quality_gates",
        )
        return
    blocked = [
        row
        for row in gates
        if row.get("status") in {"blocked", "unavailable"}
        or row.get("safe_report_mode") in {"draft_only", "internal_only"}
        or row.get("trusted_delivery_allowed") is False
    ]
    warnings = [row for row in gates if row.get("status") == "warning"]
    if blocked:
        _add_issue(
            blockers,
            category="report_quality",
            message="One or more CMO report quality gates block trusted delivery.",
            severity="high",
            affected=[str(row.get("report_key")) for row in blocked],
            next_action="resolve_report_quality_gate",
        )
    elif warnings:
        _add_issue(
            risks,
            category="report_quality",
            message="One or more CMO report quality gates warn.",
            severity="medium",
            affected=[str(row.get("report_key")) for row in warnings],
            next_action="review_report_warnings",
        )
    else:
        proven.append(_capability("report_quality_gates", "CMO report quality gates allow trusted delivery."))
    for row in gates:
        refs.append(_ref("report_quality_gate", row.get("report_key"), row))


def _evaluate_work_queue(
    queue: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    critical = [
        row
        for row in queue
        if row.get("status") in {"open", "blocked", "waiting"}
        and row.get("severity") in {"critical", "high"}
    ]
    lower = [
        row
        for row in queue
        if row.get("status") in {"open", "blocked", "waiting"}
        and row.get("severity") in {"medium", "low", "info"}
    ]
    if critical:
        _add_issue(
            blockers,
            category="work_queue",
            message="Critical or high CMO work queue items prevent passed pilot proof.",
            severity="critical",
            affected=[str(row.get("item_id")) for row in critical],
            next_action=str(critical[0].get("next_action_key") or "resolve_work_queue"),
        )
    elif lower:
        _add_issue(
            risks,
            category="work_queue",
            message="Open CMO work queue warnings remain.",
            severity="medium",
            affected=[str(row.get("item_id")) for row in lower],
            next_action="review_work_queue",
        )
    else:
        proven.append(_capability("work_queue_clear", "No critical/high CMO work queue blockers remain."))
    for row in queue:
        refs.append(_ref("work_queue", row.get("item_id"), row))


def _evaluate_drilldowns(
    drilldowns: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    if not drilldowns:
        _add_issue(
            blockers,
            category="kpi_lineage",
            message="KPI drill-down/data-lineage evidence is missing.",
            severity="high",
            next_action="attach_kpi_lineage",
        )
        return
    blocked = [
        row
        for row in drilldowns
        if row.get("production_lineage_ready") is False
        or row.get("production_lineage_status") == "blocked"
        or row.get("status") in {"blocked", "unavailable"}
    ]
    degraded = [row for row in drilldowns if row.get("status") == "degraded"]
    if blocked:
        _add_issue(
            blockers,
            category="kpi_lineage",
            message="KPI drill-down lineage blocks production proof.",
            severity="high",
            affected=[str(row.get("kpi_key")) for row in blocked],
            next_action="resolve_kpi_lineage",
        )
    elif degraded:
        _add_issue(
            risks,
            category="kpi_lineage",
            message="KPI drill-down lineage is degraded.",
            severity="medium",
            affected=[str(row.get("kpi_key")) for row in degraded],
            next_action="review_kpi_lineage",
        )
    else:
        proven.append(_capability("kpi_drilldown_lineage", "KPI drill-down lineage is production-ready."))
    for row in drilldowns:
        refs.append(_ref("kpi_drilldown", row.get("kpi_key"), row))


def _evaluate_approval_reviews(
    projection: Mapping[str, Any],
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    reviews = _dicts(projection.get("cmo_approval_reviews"))
    if not reviews:
        _add_issue(
            risks,
            category="approval_review",
            message="No approval-review evidence is attached; read-only proof may still be partial.",
            severity="low",
            next_action="attach_approval_review_evidence",
        )
        return
    blocked = [row for row in reviews if row.get("status") in {"blocked", "timed_out"}]
    if blocked:
        _add_issue(
            blockers,
            category="approval_review",
            message="Approval review evidence blocks approval-sensitive pilot actions.",
            severity="high",
            affected=[str(row.get("approval_id")) for row in blocked],
            next_action="resolve_approval_review",
        )
    else:
        proven.append(_capability("approval_review", "Approval review evidence is actionable."))
    for row in reviews:
        refs.append(_ref("approval_review", row.get("approval_id"), row))


def _evaluate_agent_contracts(
    agents: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    unproven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
) -> None:
    if not agents:
        agents = marketing_agent_contract_specs()
    for spec in agents:
        agent_type = _normalize(spec.get("agent_type") or spec.get("agent"))
        production_ready = bool(spec.get("production_ready"))
        maturity = _normalize(spec.get("maturity") or spec.get("status"))
        if production_ready:
            proven.append(_capability(f"agent:{agent_type}", f"{agent_type} contract is production-capable."))
        else:
            unproven.append(
                _capability(
                    f"agent:{agent_type}",
                    str(spec.get("blocker") or f"{agent_type} is {maturity or 'not production-ready'}."),
                    status=maturity or "not_production_ready",
                )
            )
        refs.append(_ref("agent_contract", agent_type, spec))
    present = {_normalize(spec.get("agent_type") or spec.get("agent")) for spec in agents}
    for key, reason in FIRST_CLASS_UNPROVEN_AGENT_KEYS.items():
        if key not in present:
            unproven.append(_capability(f"agent:{key}", reason, status="unavailable"))


def _evaluate_test_evidence(
    kind: str,
    evidence: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    refs: list[dict[str, Any]],
) -> None:
    if not evidence:
        _add_issue(
            risks,
            category=f"{kind}_evidence",
            message=f"No CMO {kind} test evidence refs are attached to the pilot proof.",
            severity="low",
            next_action=f"attach_{kind}_test_evidence",
        )
        return
    failures = [row for row in evidence if row.get("status") not in {"passed", "pass", "ok"}]
    if failures:
        _add_issue(
            risks,
            category=f"{kind}_evidence",
            message=f"CMO {kind} test evidence includes non-passing cases.",
            severity="medium",
            affected=[str(row.get("scenario") or row.get("test") or row.get("ref")) for row in failures],
            next_action=f"review_{kind}_test_evidence",
        )
    else:
        proven.append(_capability(f"{kind}_test_evidence", f"CMO {kind} test evidence is passing."))
    for row in evidence:
        refs.append(_ref(f"{kind}_test", row.get("ref") or row.get("scenario") or row.get("test"), row))


def _apply_environment_specific_rules(
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    report_refs: list[dict[str, Any]],
    audit_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    has_confirmed_write = any(item.get("capability_key") == "external_write_confirmation" for item in proven)
    has_report = bool(report_refs)
    has_audit = bool(audit_refs)
    has_sources = bool(source_refs)
    if env == "vendor_sandbox":
        missing = []
        if not has_confirmed_write:
            missing.append("confirmed_external_write")
        if not has_sources:
            missing.append("source_refs")
        if not has_report:
            missing.append("report_proof")
        if not has_audit:
            missing.append("audit_refs")
        if missing:
            _add_issue(
                risks,
                category="sandbox_criteria",
                message="Vendor-sandbox proof is partial until write, source, report, and audit evidence are attached.",
                severity="medium",
                affected=missing,
                next_action="attach_sandbox_completion_evidence",
            )
        next_actions.append(_next_action("attach_sandbox_completion_evidence", "Attach Sandbox Completion Evidence"))
    elif env == "real_vendor":
        missing = []
        if not has_confirmed_write:
            missing.append("confirmed_external_write")
        if not has_sources:
            missing.append("source_refs")
        if not has_report:
            missing.append("report_proof")
        if not has_audit:
            missing.append("audit_refs")
        if missing:
            _add_issue(
                blockers,
                category="real_vendor_criteria",
                message=(
                    "Real-vendor proof is blocked until critical write, source, "
                    "report, and audit evidence is present."
                ),
                severity="critical",
                affected=missing,
                next_action="attach_real_vendor_completion_evidence",
            )
        next_actions.append(_next_action("attach_real_vendor_completion_evidence", "Attach Real-Vendor Evidence"))


def _proof_status(env: str, blockers: list[dict[str, Any]], risks: list[dict[str, Any]]) -> str:
    if env == "demo":
        return "demo_only"
    if env == "test_double":
        return "test_only"
    if env == "unknown" and not blockers:
        return "unavailable"
    if blockers:
        return "blocked"
    if risks and env == "vendor_sandbox":
        return "partial"
    if risks and env != "real_vendor":
        return "partial"
    if risks and env == "real_vendor":
        low_only = all(row.get("severity") in {"low", "info"} for row in risks)
        return "passed" if low_only else "partial"
    return "passed"


def _readiness_score(
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
) -> int:
    score = 100
    score -= min(len(blockers) * 12, 84)
    score -= min(sum(4 if row.get("severity") in {"low", "info"} else 7 for row in risks), 35)
    score += min(len(proven), 8)
    if env == "demo":
        score = min(score, 30)
    elif env == "test_double":
        score = min(score, 35)
    elif env == "unknown":
        score = min(score, 60)
    elif env == "vendor_sandbox":
        score = min(score, 92)
    return max(min(score, 100), 0)


def _resolve_environment_type(
    explicit: str | None,
    source_context: Mapping[str, Any],
    contracts: list[dict[str, Any]],
) -> str:
    candidates = [
        explicit,
        source_context.get("cmo_pilot_environment_type"),
        source_context.get("pilot_environment_type"),
        source_context.get("environment_type"),
        source_context.get("environment"),
    ]
    for candidate in candidates:
        normalized = _normalize(candidate)
        if normalized in ENVIRONMENT_TYPES:
            return normalized
        if normalized in {"sandbox", "vendor_test", "vendor_sandbox"}:
            return "vendor_sandbox"
        if normalized in {"real", "production_vendor", "live_vendor"}:
            return "real_vendor"
    source = _normalize(source_context.get("source"))
    if source_context.get("demo") or source in DEMO_SOURCE_MARKERS:
        return "demo"
    if source in TEST_SOURCE_MARKERS or any(row.get("mock_or_test_double") for row in contracts):
        return "test_double"
    return "unknown"


def _connector_source_refs(
    contracts: list[dict[str, Any]],
    drilldowns: list[dict[str, Any]],
    kpis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for row in contracts:
        for source in _dicts(row.get("source_objects")):
            refs.append(_ref("connector_source", row.get("connector_key"), source))
    for row in drilldowns:
        for source in _dicts(row.get("source_refs")):
            refs.append(_ref("kpi_drilldown_source", row.get("kpi_key"), source))
    for row in kpis:
        for source in _dicts(row.get("source_refs")):
            refs.append(_ref("kpi_source", row.get("kpi_key"), source))
    return _dedupe_refs(refs)


def _report_refs(gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _ref("report_quality_gate", row.get("report_key"), row)
        for row in gates
        if row.get("status") in {"pass", "passed"} or row.get("safe_report_mode") == "deliverable"
    ]


def _audit_refs(
    audit: Mapping[str, Any],
    approvals: Mapping[str, Any],
    writes: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    kpis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for key in ("audit_ref", "audit_reference", "audit_id"):
        if audit.get(key):
            refs.append(_ref("decision_audit", audit.get(key), {key: audit.get(key)}))
    summary = audit.get("marketing_decision_audit_summary")
    if isinstance(summary, Mapping) and summary.get("status") == "ready":
        refs.append(_ref("decision_audit_summary", summary.get("schema_version"), summary))
    for row in _dicts(approvals.get("cmo_approval_reviews")):
        for value in _string_list(row.get("audit_refs")):
            refs.append(_ref("approval_audit", value, row))
    for row in writes:
        for key in ("audit_reference", "audit_ref"):
            if row.get(key):
                refs.append(_ref("external_write_audit", row.get(key), row))
    for row in reports:
        for value in _string_list(row.get("required_approval_audit_refs") or row.get("audit_refs")):
            refs.append(_ref("report_audit", value, row))
    for row in kpis:
        for value in _string_list(row.get("audit_refs") or row.get("audit_ref")):
            refs.append(_ref("kpi_audit", value, row))
    return _dedupe_refs(refs)


def _summary_next_action(status: str, blockers: list[dict[str, Any]], risks: list[dict[str, Any]]) -> str:
    if status in {"demo_only", "test_only"}:
        return "connect_real_or_vendor_sandbox"
    if blockers:
        return str(blockers[0].get("next_action") or "resolve_pilot_blockers")
    if risks:
        return str(risks[0].get("next_action") or "review_pilot_risks")
    return "none"


def _proof_id(
    tenant_id: str | None,
    company_id: str | None,
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    report_refs: list[dict[str, Any]],
) -> str:
    payload = {
        "tenant_id": tenant_id,
        "company_id": company_id,
        "environment_type": env,
        "blockers": blockers,
        "risks": risks,
        "proven": proven,
        "source_refs": source_refs,
        "report_refs": report_refs,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:20]
    return f"cmo_pilot_proof_{digest}"


def _lint_result_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        result = dict(row)
    else:
        result = {
            "has_errors": bool(getattr(row, "has_errors", False)),
            "errors": [getattr(item, "__dict__", item) for item in getattr(row, "errors", [])],
            "warnings": [getattr(item, "__dict__", item) for item in getattr(row, "warnings", [])],
            "workflow_file": getattr(row, "workflow_file", None),
            "workflow_id": getattr(row, "workflow_id", None),
        }
    return _redact(result)


def _add_issue(
    target: list[dict[str, Any]],
    *,
    category: str,
    message: str,
    severity: str,
    next_action: str,
    affected: Iterable[str] | None = None,
) -> None:
    target.append(
        {
            "category": category,
            "severity": severity,
            "message": message,
            "affected": sorted({str(item) for item in affected or [] if item}),
            "next_action": next_action,
        }
    )


def _capability(key: str, description: str, *, status: str = "proven") -> dict[str, Any]:
    return {"capability_key": key, "status": status, "description": description}


def _ref(ref_type: str, ref_id: Any, source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _redact(
        {
            "type": ref_type,
            "ref_id": _string_or_none(ref_id) or _stable_ref(ref_type, source or {}),
            "connector_key": (source or {}).get("connector_key"),
            "status": (source or {}).get("status") or (source or {}).get("readiness"),
            "source_url": (source or {}).get("source_url") or (source or {}).get("url"),
            "audit_reference": (source or {}).get("audit_reference") or (source or {}).get("audit_ref"),
        }
    )


def _next_action(action_key: str, label: str) -> dict[str, str]:
    return {"action_key": action_key, "label": label}


def _stable_ref(ref_type: str, source: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(source).encode("utf-8")).hexdigest()[:12]
    return f"{ref_type}_{digest}"


def _dedupe_issues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = f"{row.get('category')}|{row.get('message')}|{','.join(row.get('affected') or [])}"
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _dedupe_capabilities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("capability_key") or row.get("description"))
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _dedupe_refs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = f"{row.get('type')}|{row.get('ref_id')}"
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _dedupe_actions(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    output: list[dict[str, str]] = []
    for row in rows:
        key = str(row.get("action_key") or row.get("label"))
        if key in seen or key in {"", "none"}:
            continue
        seen.add(key)
        output.append(row)
    return output


def _dicts(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, list | tuple | set):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if item not in {None, ""}]
    return []


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(_redact(value), sort_keys=True, separators=(",", ":"), default=str)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if any(marker in text_key.lower() for marker in SECRET_KEY_MARKERS):
                redacted[text_key] = "[REDACTED]"
            else:
                redacted[text_key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple | set):
        return [_redact(item) for item in value]
    return value
