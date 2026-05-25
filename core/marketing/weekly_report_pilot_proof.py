"""CMO-PROD-1 weekly marketing report pilot proof.

This module turns a structured evidence bundle into a strict pilot-proof
verdict for the **read-only weekly marketing report**. It is the production-
proof gate that complements the CMO-9.4 broader pilot-proof projection.

It is intentionally narrow and fail-closed:

  * Demo / test-double / unknown environments cannot produce a real-vendor
    production claim.
  * Vendor-sandbox evidence can only produce a *sandbox* / *partial* proof
    label and must never be described as real-vendor production proof.
  * Real-vendor evidence passes only when CRM + Ads + Analytics + Email
    connectors are configured/read-ready, all required field mappings are
    valid, the historical backfill is complete for each required category,
    every required weekly-report KPI is present and not blocked, every
    reconciliation check passes, the weekly-report quality gate is `pass`
    or `passed`, and the report artifact + decision-audit refs are present.
  * Mock / test-double connector evidence and demo / sample / fallback
    source markers always block the proof.

The module is storage-free: it operates on JSON-serialisable evidence
dictionaries (or `WeeklyReportPilotEvidence` instances). Callers that want
to evaluate runtime state should hydrate the evidence dict from their own
projections before calling :func:`evaluate_weekly_marketing_report_proof`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.marketing.pilot_proof import (
    DEMO_SOURCE_MARKERS,
    ENVIRONMENT_TYPES,
    SECRET_KEY_MARKERS,
    TEST_SOURCE_MARKERS,
)

WEEKLY_REPORT_PROOF_VERSION = "2026-05-24.cmo-prod-1"

PROOF_STATUSES = (
    "passed",
    "sandbox_proven",
    "partial",
    "blocked",
    "demo_only",
    "test_only",
    "unavailable",
)

REQUIRED_CONNECTOR_CATEGORIES: tuple[str, ...] = ("CRM", "Ads", "Analytics", "Email")
REQUIRED_MAPPINGS: tuple[str, ...] = (
    "lifecycle_stages",
    "opportunity_revenue",
    "campaign_ids",
    "utm_fields",
    "consent_unsubscribe",
    "fiscal_calendar",
    "currency",
    "timezone",
)
REQUIRED_BACKFILL_CATEGORIES: tuple[str, ...] = ("CRM", "Ads", "Analytics", "Email")
REQUIRED_KPI_KEYS: tuple[str, ...] = (
    "cac",
    "mql",
    "sql",
    "mql_to_sql_conversion_rate",
    "roas",
    "pipeline_contribution",
    "conversion_rates_by_funnel_stage",
    "email_performance",
)

# Statuses we treat as "ok" for connector/mapping/backfill/KPI/reconciliation/report rows.
# enterprise-gate: process-local-ok reason=static-proof-state-set
HEALTHY_CONNECTOR_STATES = {"healthy", "ok", "ready"}
# enterprise-gate: process-local-ok reason=static-proof-state-set
VALID_MAPPING_STATES = {"valid", "ready", "configured", "ok"}
# enterprise-gate: process-local-ok reason=static-proof-state-set
COMPLETED_BACKFILL_STATES = {"completed", "ready", "ok"}
# enterprise-gate: process-local-ok reason=static-proof-state-set
HEALTHY_KPI_STATES = {"ready", "passed", "pass", "ok", "warning"}
# enterprise-gate: process-local-ok reason=static-proof-state-set
BLOCKING_KPI_STATES = {"blocked", "unavailable", "missing"}
# enterprise-gate: process-local-ok reason=static-proof-state-set
PASSED_RECON_STATES = {"pass", "passed", "ok"}
# enterprise-gate: process-local-ok reason=static-proof-state-set
WARNING_RECON_STATES = {"warning", "warn"}
# enterprise-gate: process-local-ok reason=static-proof-state-set
BLOCKING_RECON_STATES = {"blocked", "failed", "missing", "unavailable"}
# enterprise-gate: process-local-ok reason=static-proof-state-set
PASSED_REPORT_STATES = {"pass", "passed"}
# enterprise-gate: process-local-ok reason=static-proof-state-set
WARNING_REPORT_STATES = {"warning", "warn"}
# enterprise-gate: process-local-ok reason=static-proof-state-set
BLOCKING_REPORT_STATES = {"blocked", "unavailable", "missing", "failed"}


# ---------------------------------------------------------------------------
# Evidence model
# ---------------------------------------------------------------------------


@dataclass
class WeeklyReportPilotEvidence:
    """Structured evidence bundle for the weekly-report pilot proof.

    Every field is optional in the dataclass so a caller can supply only
    what they have; the validator enforces what is *required* per
    environment type.
    """

    tenant_id: str | None = None
    company_id: str | None = None
    environment_type: str = "unknown"
    connector_evidence: list[dict[str, Any]] = field(default_factory=list)
    mapping_evidence: list[dict[str, Any]] = field(default_factory=list)
    backfill_evidence: list[dict[str, Any]] = field(default_factory=list)
    kpi_results: list[dict[str, Any]] = field(default_factory=list)
    reconciliation_checks: list[dict[str, Any]] = field(default_factory=list)
    report_quality_gates: list[dict[str, Any]] = field(default_factory=list)
    report_artifact_refs: list[dict[str, Any]] = field(default_factory=list)
    decision_audit_refs: list[dict[str, Any]] = field(default_factory=list)
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str | None = None
    source_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "company_id": self.company_id,
            "environment_type": self.environment_type,
            "connector_evidence": list(self.connector_evidence),
            "mapping_evidence": list(self.mapping_evidence),
            "backfill_evidence": list(self.backfill_evidence),
            "kpi_results": list(self.kpi_results),
            "reconciliation_checks": list(self.reconciliation_checks),
            "report_quality_gates": list(self.report_quality_gates),
            "report_artifact_refs": list(self.report_artifact_refs),
            "decision_audit_refs": list(self.decision_audit_refs),
            "source_refs": list(self.source_refs),
            "generated_at": self.generated_at,
            "source_context": dict(self.source_context),
        }


def evidence_from_mapping(value: Mapping[str, Any] | WeeklyReportPilotEvidence | None) -> WeeklyReportPilotEvidence:
    """Normalise either a dict or a dataclass into ``WeeklyReportPilotEvidence``."""

    if isinstance(value, WeeklyReportPilotEvidence):
        return value
    data = value if isinstance(value, Mapping) else {}
    return WeeklyReportPilotEvidence(
        tenant_id=_string_or_none(data.get("tenant_id")),
        company_id=_string_or_none(data.get("company_id")),
        environment_type=_normalize(data.get("environment_type") or "unknown") or "unknown",
        connector_evidence=_dicts(data.get("connector_evidence")),
        mapping_evidence=_dicts(data.get("mapping_evidence") or data.get("field_mapping_status")),
        backfill_evidence=_dicts(data.get("backfill_evidence") or data.get("backfill_status")),
        kpi_results=_dicts(data.get("kpi_results") or data.get("unified_cmo_kpi_results")),
        reconciliation_checks=_dicts(data.get("reconciliation_checks") or data.get("cmo_kpi_reconciliation_checks")),
        report_quality_gates=_dicts(data.get("report_quality_gates")),
        report_artifact_refs=_dicts(data.get("report_artifact_refs") or data.get("report_artifacts")),
        decision_audit_refs=_dicts(data.get("decision_audit_refs") or data.get("audit_refs")),
        source_refs=_dicts(data.get("source_refs")),
        generated_at=_string_or_none(data.get("generated_at")),
        source_context=(
            dict(data["source_context"])
            if isinstance(data.get("source_context"), Mapping)
            else {}
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_weekly_marketing_report_proof(
    evidence: Mapping[str, Any] | WeeklyReportPilotEvidence | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a strict, deterministic weekly-report pilot-proof verdict.

    Output keys mirror the broader ``cmo_pilot_proof`` projection so the
    redacted bundle can be embedded in the same /kpis/cmo response.
    """

    bundle = evidence_from_mapping(evidence)
    generated_at = _iso(now or datetime.now(UTC))
    env = _resolve_environment(bundle)

    blockers: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    proven: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []

    _evaluate_environment(env, bundle, blockers, risks, next_actions)
    _evaluate_connectors(bundle, env, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_mappings(bundle, env, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_backfills(bundle, env, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_kpis(bundle, env, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_reconciliations(bundle, env, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_report_quality(bundle, env, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_report_artifacts(bundle, env, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_decision_audit(bundle, env, blockers, risks, proven, evidence_refs, next_actions)
    _evaluate_source_lineage(bundle, env, blockers, risks, proven, evidence_refs, next_actions)

    blockers = _dedupe_issues(blockers)
    risks = _dedupe_issues(risks)
    proven = _dedupe(proven, key=lambda row: row.get("capability_key", ""))
    evidence_refs = _dedupe(evidence_refs, key=lambda row: (row.get("type", ""), str(row.get("ref_id") or "")))
    next_actions = _dedupe(next_actions, key=lambda row: row.get("action_key", ""))

    status = _proof_status(env, blockers, risks)
    real_vendor_allowed = env == "real_vendor" and status == "passed"
    production_claim_allowed = real_vendor_allowed
    proof: dict[str, Any] = {
        "schema_version": WEEKLY_REPORT_PROOF_VERSION,
        "proof_id": _proof_id(bundle, env, blockers, proven, evidence_refs),
        "tenant_id": bundle.tenant_id,
        "company_id": bundle.company_id,
        "environment_type": env,
        "proof_scope": "sandbox" if env == "vendor_sandbox" else env,
        "proof_status": status,
        "production_claim_allowed": production_claim_allowed,
        "real_vendor_claim_allowed": real_vendor_allowed,
        "readiness_score": _readiness_score(env, blockers, risks, proven),
        "blockers": blockers,
        "risks": risks,
        "proven_capabilities": proven,
        "evidence_refs": evidence_refs,
        "next_actions": next_actions,
        "generated_at": generated_at,
        "evidence_input": bundle.to_dict(),
    }
    return _redact(proof)


def summarize_weekly_marketing_report_proof(proof: Mapping[str, Any]) -> dict[str, Any]:
    status = str(proof.get("proof_status") or "unavailable")
    blockers = _dicts(proof.get("blockers"))
    risks = _dicts(proof.get("risks"))
    return {
        "schema_version": WEEKLY_REPORT_PROOF_VERSION,
        "proof_id": proof.get("proof_id"),
        "environment_type": proof.get("environment_type") or "unknown",
        "proof_status": status,
        "readiness_score": proof.get("readiness_score") or 0,
        "production_claim_allowed": bool(proof.get("production_claim_allowed")),
        "real_vendor_claim_allowed": bool(proof.get("real_vendor_claim_allowed")),
        "proven_capabilities": len(_dicts(proof.get("proven_capabilities"))),
        "blockers": len(blockers),
        "risks": len(risks),
        "next_action_cta": _summary_next_action(status, blockers, risks),
    }


def build_weekly_marketing_report_evidence_bundle(proof: Mapping[str, Any]) -> dict[str, Any]:
    redacted = _redact(dict(proof))
    return {
        "bundle_version": WEEKLY_REPORT_PROOF_VERSION,
        "bundle_type": "weekly_marketing_report_pilot_proof",
        "proof_id": redacted.get("proof_id"),
        "environment_type": redacted.get("environment_type"),
        "proof_status": redacted.get("proof_status"),
        "generated_at": redacted.get("generated_at"),
        "summary": summarize_weekly_marketing_report_proof(redacted),
        "evidence": {
            "proven_capabilities": redacted.get("proven_capabilities") or [],
            "blockers": redacted.get("blockers") or [],
            "risks": redacted.get("risks") or [],
            "evidence_refs": redacted.get("evidence_refs") or [],
            "next_actions": redacted.get("next_actions") or [],
        },
    }


def serialize_weekly_marketing_report_evidence_bundle(bundle: Mapping[str, Any]) -> str:
    return json.dumps(_redact(bundle), sort_keys=True, separators=(",", ":"), default=str)


def build_weekly_marketing_report_proof_projection(
    evidence: Mapping[str, Any] | WeeklyReportPilotEvidence | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the /kpis/cmo-shaped projection for the weekly report proof."""

    proof = evaluate_weekly_marketing_report_proof(evidence, now=now)
    return {
        "weekly_report_pilot_proof_version": WEEKLY_REPORT_PROOF_VERSION,
        "weekly_report_pilot_proof": proof,
        "weekly_report_pilot_proof_summary": summarize_weekly_marketing_report_proof(proof),
        "weekly_report_pilot_evidence_bundle": build_weekly_marketing_report_evidence_bundle(proof),
    }


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------


def _evaluate_environment(
    env: str,
    bundle: WeeklyReportPilotEvidence,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    if env == "demo":
        _add_issue(
            blockers,
            category="environment",
            message="Demo/sample evidence cannot produce a weekly-report production proof.",
            severity="critical",
            next_action="connect_real_or_sandbox_sources",
        )
        next_actions.append(_next_action("connect_real_or_sandbox_sources", "Connect Real or Sandbox Sources"))
    elif env == "test_double":
        _add_issue(
            blockers,
            category="environment",
            message="Test-double / mock evidence cannot produce a weekly-report production proof.",
            severity="critical",
            next_action="replace_test_doubles_with_vendor_sandbox",
        )
        next_actions.append(
            _next_action("replace_test_doubles_with_vendor_sandbox", "Replace Test Doubles With Vendor Sandbox")
        )
    elif env == "unknown":
        _add_issue(
            risks,
            category="environment",
            message="Weekly-report pilot environment type is unknown.",
            severity="medium",
            next_action="label_pilot_environment",
        )
        next_actions.append(_next_action("label_pilot_environment", "Label Pilot Environment"))

    for row in bundle.connector_evidence:
        if row.get("mock_or_test_double") or row.get("stub_only"):
            _add_issue(
                blockers,
                category="environment",
                message="At least one connector evidence row is marked mock/test-double.",
                severity="critical",
                next_action="replace_test_doubles_with_vendor_sandbox",
            )
            break


def _evaluate_connectors(
    bundle: WeeklyReportPilotEvidence,
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    rows = bundle.connector_evidence
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        category = _category(row)
        if category:
            by_category.setdefault(category, []).append(row)

    missing: list[str] = []
    not_ready: list[str] = []
    for required in REQUIRED_CONNECTOR_CATEGORIES:
        configured = by_category.get(required, [])
        if not configured:
            missing.append(required)
            continue
        ready_rows = [
            row
            for row in configured
            if _normalize(row.get("health_status") or row.get("status")) in HEALTHY_CONNECTOR_STATES
            and bool(row.get("read_ready", row.get("configured", True)))
            and not (row.get("mock_or_test_double") or row.get("stub_only"))
        ]
        if not ready_rows:
            not_ready.append(required)
        for row in configured:
            evidence_refs.append(_ref("connector", row.get("connector_key") or row.get("key"), row))

    if missing:
        _add_issue(
            blockers,
            category="connector",
            message=(
                f"Required connector categor{'ies' if len(missing) > 1 else 'y'} "
                f"{', '.join(missing)} not present in pilot evidence."
            ),
            severity="critical",
            affected=missing,
            next_action="connect_real_or_sandbox_sources",
        )
        next_actions.append(_next_action("connect_real_or_sandbox_sources", "Connect Real or Sandbox Sources"))
    if not_ready:
        _add_issue(
            blockers,
            category="connector",
            message=(
                f"Required connector categor{'ies' if len(not_ready) > 1 else 'y'} "
                f"{', '.join(not_ready)} are configured but not read-ready."
            ),
            severity="high",
            affected=not_ready,
            next_action="resolve_connector_readiness",
        )
    if not missing and not not_ready:
        proven.append(
            _capability(
                "weekly_report_connectors",
                "CRM, Ads, Analytics, and Email connectors are present and read-ready.",
            )
        )


def _evaluate_mappings(
    bundle: WeeklyReportPilotEvidence,
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    by_key: dict[str, dict[str, Any]] = {}
    for row in bundle.mapping_evidence:
        key = _normalize(row.get("key") or row.get("mapping_key"))
        if key:
            by_key[key] = row
        evidence_refs.append(_ref("mapping", key or row.get("name"), row))

    missing: list[str] = []
    invalid: list[str] = []
    for required in REQUIRED_MAPPINGS:
        row = by_key.get(required)
        if row is None:
            missing.append(required)
            continue
        status = _normalize(row.get("status") or row.get("state"))
        if status not in VALID_MAPPING_STATES:
            invalid.append(required)

    if missing:
        _add_issue(
            blockers,
            category="mapping",
            message=f"Required field mapping(s) missing: {', '.join(missing)}.",
            severity="critical",
            affected=missing,
            next_action="configure_field_mapping",
        )
        next_actions.append(_next_action("configure_field_mapping", "Configure Required Field Mappings"))
    if invalid:
        _add_issue(
            blockers,
            category="mapping",
            message=f"Required field mapping(s) not valid: {', '.join(invalid)}.",
            severity="high",
            affected=invalid,
            next_action="repair_field_mapping",
        )
    if not missing and not invalid:
        proven.append(_capability("weekly_report_mappings", "All required field mappings are valid."))


def _evaluate_backfills(
    bundle: WeeklyReportPilotEvidence,
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in bundle.backfill_evidence:
        category = _category(row)
        if category:
            by_category.setdefault(category, []).append(row)
        backfill_ref = (
            row.get("source_connector_key")
            or row.get("connector_key")
            or category
        )
        evidence_refs.append(_ref("backfill", backfill_ref, row))

    missing: list[str] = []
    incomplete: list[str] = []
    for required in REQUIRED_BACKFILL_CATEGORIES:
        rows = by_category.get(required, [])
        if not rows:
            missing.append(required)
            continue
        completed = [row for row in rows if _normalize(row.get("status")) in COMPLETED_BACKFILL_STATES]
        if not completed:
            incomplete.append(required)

    if missing:
        _add_issue(
            blockers,
            category="backfill",
            message=f"Historical backfill evidence missing for: {', '.join(missing)}.",
            severity="critical",
            affected=missing,
            next_action="complete_required_backfill",
        )
        next_actions.append(_next_action("complete_required_backfill", "Complete Required Backfill"))
    if incomplete:
        _add_issue(
            blockers,
            category="backfill",
            message=f"Historical backfill not complete for: {', '.join(incomplete)}.",
            severity="high",
            affected=incomplete,
            next_action="complete_required_backfill",
        )
    if not missing and not incomplete:
        proven.append(_capability("weekly_report_backfill", "Required CRM/Ads/Analytics/Email backfill complete."))


def _evaluate_kpis(
    bundle: WeeklyReportPilotEvidence,
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    by_key: dict[str, dict[str, Any]] = {}
    for row in bundle.kpi_results:
        key = _normalize(row.get("kpi_key") or row.get("key"))
        if key:
            by_key[key] = row
        evidence_refs.append(_ref("kpi", key or row.get("name"), row))

    missing: list[str] = []
    blocked: list[str] = []
    degraded: list[str] = []
    for required in REQUIRED_KPI_KEYS:
        row = by_key.get(required)
        if row is None:
            missing.append(required)
            continue
        status = _normalize(row.get("status") or row.get("readiness"))
        if status in BLOCKING_KPI_STATES:
            blocked.append(required)
        elif status not in HEALTHY_KPI_STATES:
            degraded.append(required)

    if missing:
        _add_issue(
            blockers,
            category="kpi",
            message=f"Required weekly-report KPI(s) missing: {', '.join(missing)}.",
            severity="critical",
            affected=missing,
            next_action="compute_required_weekly_report_kpis",
        )
        next_actions.append(
            _next_action("compute_required_weekly_report_kpis", "Compute Required Weekly Report KPIs")
        )
    if blocked:
        _add_issue(
            blockers,
            category="kpi",
            message=f"Required weekly-report KPI(s) blocked: {', '.join(blocked)}.",
            severity="high",
            affected=blocked,
            next_action="resolve_blocked_weekly_report_kpis",
        )
    if degraded:
        _add_issue(
            risks,
            category="kpi",
            message=f"Required weekly-report KPI(s) degraded: {', '.join(degraded)}.",
            severity="medium",
            affected=degraded,
            next_action="review_degraded_weekly_report_kpis",
        )
    if not missing and not blocked:
        proven.append(_capability("weekly_report_kpis", "Required weekly-report KPIs are present and not blocked."))


def _evaluate_reconciliations(
    bundle: WeeklyReportPilotEvidence,
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    rows = bundle.reconciliation_checks
    if not rows:
        _add_issue(
            blockers,
            category="reconciliation",
            message="No KPI reconciliation check evidence attached.",
            severity="high",
            next_action="attach_reconciliation_checks",
        )
        next_actions.append(_next_action("attach_reconciliation_checks", "Attach Reconciliation Checks"))
        return
    failed: list[str] = []
    warning: list[str] = []
    passed: list[str] = []
    for row in rows:
        status = _normalize(row.get("status"))
        key = str(row.get("check_key") or row.get("key") or row.get("name") or "")
        evidence_refs.append(_ref("reconciliation", key or row.get("kpi_key"), row))
        if status in BLOCKING_RECON_STATES:
            failed.append(key or "unknown_check")
        elif status in WARNING_RECON_STATES:
            warning.append(key or "unknown_check")
        elif status in PASSED_RECON_STATES:
            passed.append(key)
    if failed:
        _add_issue(
            blockers,
            category="reconciliation",
            message=f"KPI reconciliation failed for: {', '.join(failed)}.",
            severity="critical",
            affected=failed,
            next_action="resolve_failed_reconciliations",
        )
        next_actions.append(_next_action("resolve_failed_reconciliations", "Resolve Failed Reconciliations"))
    if warning:
        _add_issue(
            risks,
            category="reconciliation",
            message=f"KPI reconciliation warnings for: {', '.join(warning)}.",
            severity="medium",
            affected=warning,
            next_action="review_reconciliation_warnings",
        )
    if passed and not failed:
        proven.append(_capability("weekly_report_reconciliation", "All KPI reconciliation checks pass."))


def _evaluate_report_quality(
    bundle: WeeklyReportPilotEvidence,
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    rows = bundle.report_quality_gates
    if not rows:
        _add_issue(
            blockers,
            category="report_quality",
            message="Report quality gate evidence is missing for weekly_marketing_report.",
            severity="critical",
            next_action="attach_report_quality_gate",
        )
        next_actions.append(_next_action("attach_report_quality_gate", "Attach Report Quality Gate"))
        return
    relevant = [
        row
        for row in rows
        if _normalize(row.get("report_key") or row.get("key")) == "weekly_marketing_report"
    ]
    if not relevant:
        relevant = rows
    pass_rows = [row for row in relevant if _normalize(row.get("status")) in PASSED_REPORT_STATES]
    warning_rows = [row for row in relevant if _normalize(row.get("status")) in WARNING_REPORT_STATES]
    blocked_rows = [row for row in relevant if _normalize(row.get("status")) in BLOCKING_REPORT_STATES]
    for row in relevant:
        evidence_refs.append(_ref("report_quality_gate", row.get("report_key") or row.get("key"), row))
    if blocked_rows:
        _add_issue(
            blockers,
            category="report_quality",
            message="Weekly marketing report quality gate is blocked.",
            severity="critical",
            next_action=str(blocked_rows[0].get("next_action_cta") or "fix_weekly_report_quality_gate"),
        )
        next_actions.append(_next_action("fix_weekly_report_quality_gate", "Fix Weekly Report Quality Gate"))
    elif not pass_rows:
        _add_issue(
            blockers,
            category="report_quality",
            message="Weekly marketing report quality gate has no passing evidence.",
            severity="high",
            next_action="run_weekly_report_quality_gate",
        )
    elif warning_rows:
        _add_issue(
            risks,
            category="report_quality",
            message="Weekly marketing report quality gate has warnings.",
            severity="medium",
            next_action="review_weekly_report_warnings",
        )
        proven.append(_capability("weekly_report_quality_gate", "Weekly report quality gate has passing evidence."))
    else:
        proven.append(_capability("weekly_report_quality_gate", "Weekly report quality gate passes."))


def _evaluate_report_artifacts(
    bundle: WeeklyReportPilotEvidence,
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    rows = bundle.report_artifact_refs
    if not rows:
        category = blockers if env == "real_vendor" else risks
        _add_issue(
            category,
            category="report_artifact",
            message="No weekly-report artifact reference attached to pilot evidence.",
            severity="high" if env == "real_vendor" else "medium",
            next_action="attach_weekly_report_artifact",
        )
        next_actions.append(_next_action("attach_weekly_report_artifact", "Attach Weekly Report Artifact"))
        return
    for row in rows:
        evidence_refs.append(_ref("report_artifact", row.get("artifact_id") or row.get("id"), row))
    proven.append(_capability("weekly_report_artifact", "Weekly-report artifact reference attached."))


def _evaluate_decision_audit(
    bundle: WeeklyReportPilotEvidence,
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    rows = bundle.decision_audit_refs
    if not rows:
        category = blockers if env == "real_vendor" else risks
        _add_issue(
            category,
            category="decision_audit",
            message="No decision-audit reference attached to weekly-report pilot evidence.",
            severity="high" if env == "real_vendor" else "medium",
            next_action="attach_decision_audit_ref",
        )
        next_actions.append(_next_action("attach_decision_audit_ref", "Attach Decision Audit Reference"))
        return
    for row in rows:
        audit_ref_id = (
            row.get("audit_id") or row.get("audit_reference") or row.get("id")
        )
        evidence_refs.append(_ref("decision_audit", audit_ref_id, row))
    proven.append(_capability("weekly_report_audit", "Decision-audit references attached."))


def _evaluate_source_lineage(
    bundle: WeeklyReportPilotEvidence,
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> None:
    rows = bundle.source_refs
    if not rows:
        category = blockers if env == "real_vendor" else risks
        _add_issue(
            category,
            category="source_lineage",
            message="Weekly-report pilot evidence has no real connector / source lineage refs.",
            severity="high" if env == "real_vendor" else "medium",
            next_action="connect_real_or_sandbox_sources",
        )
        return
    for row in rows:
        source = _normalize(row.get("source") or row.get("connector_key"))
        if source in DEMO_SOURCE_MARKERS:
            _add_issue(
                blockers,
                category="source_lineage",
                message="Demo/sample/mock source markers cannot back a production pilot proof.",
                severity="critical",
                next_action="connect_real_or_sandbox_sources",
            )
            return
        if source in TEST_SOURCE_MARKERS:
            _add_issue(
                blockers,
                category="source_lineage",
                message="Test-double source markers cannot back a production pilot proof.",
                severity="critical",
                next_action="replace_test_doubles_with_vendor_sandbox",
            )
            return
        evidence_refs.append(_ref("source", row.get("ref_id") or row.get("type"), row))
    proven.append(_capability("weekly_report_source_lineage", "Real source lineage refs attached."))


# ---------------------------------------------------------------------------
# Status / scoring
# ---------------------------------------------------------------------------


def _proof_status(env: str, blockers: list[dict[str, Any]], risks: list[dict[str, Any]]) -> str:
    if env == "demo":
        return "demo_only"
    if env == "test_double":
        return "test_only"
    if env == "unknown" and not blockers:
        return "unavailable"
    if blockers:
        return "blocked"
    if env == "vendor_sandbox":
        return "sandbox_proven" if not risks else "partial"
    if env == "real_vendor":
        if risks and any(row.get("severity") in {"high", "critical"} for row in risks):
            return "partial"
        return "passed"
    return "partial"


def _readiness_score(
    env: str,
    blockers: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    proven: list[dict[str, Any]],
) -> int:
    score = 100
    score -= min(len(blockers) * 12, 84)
    score -= min(
        sum(7 if row.get("severity") in {"high", "critical"} else 4 for row in risks),
        35,
    )
    score += min(len(proven), 8)
    if env == "demo":
        score = min(score, 25)
    elif env == "test_double":
        score = min(score, 30)
    elif env == "unknown":
        score = min(score, 55)
    elif env == "vendor_sandbox":
        score = min(score, 90)
    return max(min(score, 100), 0)


def _resolve_environment(bundle: WeeklyReportPilotEvidence) -> str:
    explicit = _normalize(bundle.environment_type)
    if explicit in ENVIRONMENT_TYPES:
        return explicit
    if explicit in {"sandbox", "vendor_test"}:
        return "vendor_sandbox"
    if explicit in {"real", "production_vendor", "live_vendor"}:
        return "real_vendor"
    source = _normalize(bundle.source_context.get("source"))
    if bundle.source_context.get("demo") or source in DEMO_SOURCE_MARKERS:
        return "demo"
    if source in TEST_SOURCE_MARKERS:
        return "test_double"
    return "unknown"


def _summary_next_action(status: str, blockers: list[dict[str, Any]], risks: list[dict[str, Any]]) -> str:
    if status in {"demo_only", "test_only"}:
        return "connect_real_or_sandbox_sources"
    if blockers:
        return str(blockers[0].get("next_action") or "resolve_weekly_report_blockers")
    if risks:
        return str(risks[0].get("next_action") or "review_weekly_report_risks")
    return "none"


def _proof_id(
    bundle: WeeklyReportPilotEvidence,
    env: str,
    blockers: list[dict[str, Any]],
    proven: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
) -> str:
    payload = {
        "tenant_id": bundle.tenant_id,
        "company_id": bundle.company_id,
        "environment_type": env,
        "blockers": [row.get("category") for row in blockers],
        "proven": [row.get("capability_key") for row in proven],
        "evidence_refs": [(row.get("type"), row.get("ref_id")) for row in evidence_refs],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"wkly_report_proof_{digest}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_issue(
    bucket: list[dict[str, Any]],
    *,
    category: str,
    message: str,
    severity: str,
    next_action: str,
    affected: Iterable[str] | None = None,
) -> None:
    bucket.append(
        {
            "category": category,
            "message": message,
            "severity": severity,
            "next_action": next_action,
            "affected": sorted({str(item) for item in (affected or [])}),
        }
    )


def _capability(key: str, description: str) -> dict[str, Any]:
    return {"capability_key": key, "description": description, "status": "proven"}


def _ref(ref_type: str, ref_id: Any, source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": ref_type,
        "ref_id": str(ref_id) if ref_id is not None else None,
        "source": dict(source) if isinstance(source, Mapping) else {},
    }


def _next_action(action_key: str, label: str) -> dict[str, str]:
    return {"action_key": action_key, "label": label}


def _category(row: Mapping[str, Any]) -> str:
    raw = (
        row.get("category")
        or row.get("connector_category")
        or row.get("source_category")
    )
    return str(raw or "").strip()


def _dedupe(rows: list[dict[str, Any]], *, key) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        marker = key(row)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(row)
    return result


def _dedupe_issues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        marker = (
            str(row.get("category") or ""),
            str(row.get("message") or ""),
            str(row.get("severity") or ""),
        )
        if marker in seen:
            continue
        seen.add(marker)
        result.append(row)
    return result


def _dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, list | tuple | set):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


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
