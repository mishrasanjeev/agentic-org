"""Persistence helper for weekly-marketing-report pilot proof (CMO-PROD-2).

This module is the only path that should write rows into the
``weekly_report_pilot_proofs`` table. It:

  1. accepts a ``WeeklyReportPilotEvidence`` instance or an evidence-bundle
     -shaped dict;
  2. invokes the authoritative CMO-PROD-1 validator
     (``evaluate_weekly_marketing_report_proof``) to produce the verdict;
  3. redacts secret/token-shaped keys from both the evidence bundle and
     the verdict before persistence;
  4. writes a new ``WeeklyReportPilotProof`` row;
  5. exposes a "latest by tenant + company" retrieval used by ``/kpis/cmo``.

It never re-implements the validator. CMO-PROD-1 remains the authority
for what counts as production-claim-allowed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.marketing.weekly_report_pilot_proof import (
    SECRET_KEY_MARKERS,
    WeeklyReportPilotEvidence,
    build_weekly_marketing_report_evidence_bundle,
    evaluate_weekly_marketing_report_proof,
    evidence_from_mapping,
    summarize_weekly_marketing_report_proof,
)
from core.models.weekly_report_pilot_proof import WeeklyReportPilotProof

logger = logging.getLogger(__name__)


async def persist_weekly_report_pilot_proof(
    session: AsyncSession,
    *,
    tenant_id: str | uuid.UUID,
    company_id: str | uuid.UUID | None = None,
    evidence: Mapping[str, Any] | WeeklyReportPilotEvidence,
    now: datetime | None = None,
) -> WeeklyReportPilotProof:
    """Evaluate ``evidence`` and persist the verdict.

    Returns the inserted ORM row. Does **not** commit — the caller decides
    transaction boundaries (matches the existing repo conventions for
    ``get_tenant_session`` / ``get_session``).
    """

    evaluated_at = (now or datetime.now(UTC))
    bundle = evidence_from_mapping(evidence)
    if not bundle.tenant_id:
        bundle.tenant_id = str(tenant_id)
    if not bundle.company_id and company_id is not None:
        bundle.company_id = str(company_id)

    proof = evaluate_weekly_marketing_report_proof(bundle, now=evaluated_at)
    evidence_bundle = build_weekly_marketing_report_evidence_bundle(proof)

    row = WeeklyReportPilotProof(
        tenant_id=_coerce_uuid(tenant_id, allow_none=False),
        company_id=_coerce_uuid(company_id, allow_none=True),
        proof_id=str(proof.get("proof_id") or ""),
        environment_type=str(proof.get("environment_type") or "unknown"),
        proof_status=str(proof.get("proof_status") or "unavailable"),
        production_claim_allowed=bool(proof.get("production_claim_allowed")),
        real_vendor_claim_allowed=bool(proof.get("real_vendor_claim_allowed")),
        readiness_score=int(proof.get("readiness_score") or 0),
        evaluated_at=evaluated_at,
        evidence_bundle=_redact(evidence_bundle),
        verdict=_redact(_strip_internal_evidence(proof)),
        blockers=_redact(list(proof.get("blockers") or [])),
        next_actions=_redact(list(proof.get("next_actions") or [])),
        report_artifact_refs=_redact(list(bundle.report_artifact_refs)),
        decision_audit_refs=_redact(list(bundle.decision_audit_refs)),
    )
    session.add(row)
    await session.flush()
    return row


async def latest_weekly_report_pilot_proof(
    session: AsyncSession,
    *,
    tenant_id: str | uuid.UUID,
    company_id: str | uuid.UUID | None = None,
) -> WeeklyReportPilotProof | None:
    """Return the newest persisted verdict for ``tenant_id`` / ``company_id``."""

    stmt = select(WeeklyReportPilotProof).where(
        WeeklyReportPilotProof.tenant_id == _coerce_uuid(tenant_id, allow_none=False)
    )
    if company_id is not None:
        stmt = stmt.where(
            WeeklyReportPilotProof.company_id == _coerce_uuid(company_id, allow_none=True)
        )
    stmt = stmt.order_by(desc(WeeklyReportPilotProof.evaluated_at)).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def serialize_persisted_proof(row: WeeklyReportPilotProof | None) -> dict[str, Any] | None:
    """Project a persisted row into a JSON-friendly dict for the API layer."""

    if row is None:
        return None
    return {
        "proof_id": row.proof_id,
        "tenant_id": str(row.tenant_id) if row.tenant_id else None,
        "company_id": str(row.company_id) if row.company_id else None,
        "environment_type": row.environment_type,
        "proof_status": row.proof_status,
        "production_claim_allowed": bool(row.production_claim_allowed),
        "real_vendor_claim_allowed": bool(row.real_vendor_claim_allowed),
        "readiness_score": int(row.readiness_score or 0),
        "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
        "evidence_bundle": row.evidence_bundle,
        "verdict": row.verdict,
        "blockers": row.blockers,
        "next_actions": row.next_actions,
        "report_artifact_refs": row.report_artifact_refs,
        "decision_audit_refs": row.decision_audit_refs,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def summarize_persisted_proof(row: WeeklyReportPilotProof | None) -> dict[str, Any] | None:
    """Compact summary for the CMO dashboard."""

    if row is None:
        return None
    summary = {}
    if isinstance(row.verdict, Mapping):
        summary = summarize_weekly_marketing_report_proof(row.verdict)
    return {
        "proof_id": row.proof_id,
        "environment_type": row.environment_type,
        "proof_status": row.proof_status,
        "production_claim_allowed": bool(row.production_claim_allowed),
        "real_vendor_claim_allowed": bool(row.real_vendor_claim_allowed),
        "readiness_score": int(row.readiness_score or 0),
        "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
        "blockers": len(row.blockers or []),
        "next_action_cta": summary.get("next_action_cta", "none"),
    }


def build_weekly_report_evidence_from_report_output(
    *,
    tenant_id: str | None,
    company_id: str | None,
    report_id: str,
    report_data: Mapping[str, Any] | None,
    rendered_paths: Iterable[str] | None = None,
    environment_type: str | None = None,
    now: datetime | None = None,
) -> WeeklyReportPilotEvidence:
    """Hydrate a ``WeeklyReportPilotEvidence`` from a successful weekly report run.

    The CMO weekly report generator runs against ``/kpis/cmo`` (or its
    fallback). When the response is real, ``report_data`` already
    carries the CMO-PROD-1 evidence we need: connector setup, mapping +
    backfill status, unified KPI results, reconciliation checks, and
    report quality gates. This function shapes those projections into
    the evidence model so the persistence helper can write a verdict.
    """

    data = dict(report_data or {})
    generated_at = (now or datetime.now(UTC)).isoformat()
    rendered = [path for path in (rendered_paths or []) if path]
    artifact_refs: list[dict[str, Any]] = [
        {
            "artifact_id": report_id,
            "path": path,
            "format": _format_from_path(path),
            "delivered_at": generated_at,
        }
        for path in rendered
    ]
    audit_refs: list[dict[str, Any]] = []
    quality_gate = data.get("report_quality_gate")
    if isinstance(quality_gate, Mapping):
        for ref in quality_gate.get("required_approval_audit_refs") or []:
            audit_refs.append({"audit_id": str(ref), "event_type": "report_quality_gate"})
    if rendered:
        audit_refs.append(
            {
                "audit_id": f"weekly_report:{report_id}",
                "event_type": "weekly_report_delivered",
                "generated_at": generated_at,
            }
        )
    if isinstance(data.get("demo"), bool) and data["demo"] and environment_type is None:
        environment_type = "demo"
    resolved_env = environment_type or data.get("environment_type") or "unknown"
    return WeeklyReportPilotEvidence(
        tenant_id=tenant_id,
        company_id=company_id,
        environment_type=str(resolved_env),
        connector_evidence=_dicts(data.get("connector_setup")),
        mapping_evidence=_dicts(data.get("field_mapping_status")),
        backfill_evidence=_dicts(data.get("backfill_status")),
        kpi_results=_dicts(data.get("unified_cmo_kpi_results")),
        reconciliation_checks=_dicts(data.get("cmo_kpi_reconciliation_checks")),
        report_quality_gates=_dicts(data.get("report_quality_gates")),
        report_artifact_refs=artifact_refs,
        decision_audit_refs=audit_refs,
        source_refs=_dicts(data.get("weekly_report_source_refs")),
        generated_at=generated_at,
        source_context={"source": data.get("source")} if data.get("source") else {},
    )


async def evaluate_and_persist_weekly_report_pilot_proof_from_report_output(
    *,
    tenant_id: str | uuid.UUID,
    company_id: str | uuid.UUID | None,
    report_id: str,
    report_data: Mapping[str, Any] | None,
    rendered_paths: Iterable[str] | None = None,
    environment_type: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Async convenience helper: hydrate evidence, evaluate, persist, summarise.

    Used by the weekly-report Celery path. Returns the verdict summary so
    the caller can log it without needing to re-query the DB.
    """

    from core.database import get_tenant_session  # local import keeps unit tests light

    evidence = build_weekly_report_evidence_from_report_output(
        tenant_id=str(tenant_id) if tenant_id is not None else None,
        company_id=str(company_id) if company_id is not None else None,
        report_id=report_id,
        report_data=report_data,
        rendered_paths=rendered_paths,
        environment_type=environment_type,
        now=now,
    )
    async with get_tenant_session(_coerce_uuid(tenant_id, allow_none=False)) as session:
        row = await persist_weekly_report_pilot_proof(
            session,
            tenant_id=tenant_id,
            company_id=company_id,
            evidence=evidence,
            now=now,
        )
        return summarize_persisted_proof(row) or {}


def persist_weekly_report_pilot_proof_from_report_output_sync(
    *,
    tenant_id: str,
    company_id: str | None,
    report_id: str,
    report_data: Mapping[str, Any] | None,
    rendered_paths: Iterable[str] | None = None,
    environment_type: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Sync wrapper for the Celery report task.

    Returns the verdict summary or ``None`` if persistence was skipped
    (DB unavailable, invalid UUID, no event loop available, etc.).
    Errors are logged and swallowed so the report task itself does not
    fail because pilot-proof persistence couldn't run.
    """

    try:
        _coerce_uuid(tenant_id, allow_none=False)
    except ValueError:
        logger.info(
            "weekly_report_pilot_proof_skipped",
            extra={"reason": "non_uuid_tenant_id", "tenant_id": str(tenant_id)},
        )
        return None
    try:
        company_uuid = _coerce_uuid(company_id, allow_none=True)
    except ValueError:
        company_uuid = None
    try:
        return asyncio.run(
            evaluate_and_persist_weekly_report_pilot_proof_from_report_output(
                tenant_id=tenant_id,
                company_id=str(company_uuid) if company_uuid else None,
                report_id=report_id,
                report_data=report_data,
                rendered_paths=rendered_paths,
                environment_type=environment_type,
                now=now,
            )
        )
    # enterprise-gate: broad-except-ok reason=pilot-proof-persistence-must-not-fail-report-delivery
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "weekly_report_pilot_proof_persist_failed",
            extra={"error": str(exc), "report_id": report_id},
        )
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_from_path(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".pdf"):
        return "pdf"
    if lowered.endswith(".xlsx") or lowered.endswith(".xls"):
        return "excel"
    if lowered.endswith(".html") or lowered.endswith(".htm"):
        return "html"
    return "binary"


def _dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, list | tuple | set):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _coerce_uuid(value: Any, *, allow_none: bool) -> uuid.UUID | None:
    if value in (None, ""):
        if allow_none:
            return None
        raise ValueError("tenant_id is required for weekly-report pilot proof persistence")
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid UUID for weekly-report pilot proof: {value!r}") from exc


def _strip_internal_evidence(proof: Mapping[str, Any]) -> dict[str, Any]:
    """Drop the verbose ``evidence_input`` from the persisted verdict.

    ``evidence_input`` is already represented in the redacted bundle stored
    alongside the verdict; keeping it in both columns just inflates row
    size and risks leaking unredacted nested dicts.
    """

    out = dict(proof)
    out.pop("evidence_input", None)
    return out


def _redact(value: Any) -> Any:
    """Belt-and-braces redaction mirroring the validator's redactor.

    The validator already redacts before returning. This pass guarantees
    that anything the caller added to the persisted row (e.g. directly
    constructed dicts) is also redacted before it lands in JSONB.
    """

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key).lower()
            if any(marker in text_key for marker in SECRET_KEY_MARKERS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple | set):
        return [_redact(item) for item in value]
    return value
