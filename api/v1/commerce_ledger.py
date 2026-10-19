# SPDX-License-Identifier: Apache-2.0
"""Commerce ledger routes: read the ledger and arbiter decisions, annotate them, export, retain.

Everything is tenant-scoped through the authenticated tenant (``get_current_tenant``)
and the tenant-bound session; a body that names another tenant is refused.
Write routes declare their ``audit_event`` and record it on the commerce ledger in
the same transaction as the write (``core/commerce/ledger.py``).

An operator decision annotates an arbiter decision: it is stored in
``oacp_operator_decision_records`` bound to the decision row
(``review_packet_id`` is the arbiter ``decision_id``) and sealed to the ledger.
It never changes the arbiter outcome, never makes a refusal consumable and never
executes anything: the request body has no outcome or execution member, the
stored record carries ``allowed_to_execute: false``, and the decision row is
append-only. See ``docs/oacp/arbiter-ledger.md``.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.commerce.arbiter_store import get_decision
from core.commerce.ledger import commerce_payload_hash, ledger_view, list_commerce_events, record_commerce_event
from core.commerce.ledger_export import build_export_bundle
from core.commerce.oacp_artifacts import (
    DurableOacpAuditReviewManifestRepository,
    DurableOacpOperatorDecisionRepository,
    DurableOacpRetentionDispositionDecisionRepository,
    OacpAuditReviewManifestRepositoryQuery,
    OacpOperatorDecisionRepositoryQuery,
    OacpRetentionDispositionDecisionRepositoryQuery,
    build_oacp_c6y1_audit_export_review_manifest,
    build_oacp_c6y5_retention_disposition_decision_record,
)
from core.commerce.oacp_retention import due_dispositions, due_manifests
from core.database import get_tenant_session

router = APIRouter(
    prefix="/commerce/runtime/ledger",
    tags=["Commerce Ledger"],
    dependencies=[require_tenant_admin],
)

DECISION_ID_PATTERN = r"^arbdec_[0-9a-f]{40}$"
OPERATOR_DECISION_EVENT = "commerce.runtime.ledger.operator_decision.create"
MANIFEST_EVENT = "commerce.runtime.ledger.manifest.create"
DISPOSITION_EVENT = "commerce.runtime.ledger.retention.disposition.create"
# The operator decision kinds that only annotate. The approve_future_* kinds belong to
# cache maintenance and block_unsafe_action would read as a veto: neither is accepted here.
OperatorAnnotationKind = Literal["request_more_evidence", "escalate_to_human_support", "defer_until_freshness_update"]
_ANNOTATION_LABELS: dict[str, str] = {
    "request_more_evidence": "operator_annotation_request_more_evidence_review_only",
    "escalate_to_human_support": "operator_annotation_escalated_review_only",
    "defer_until_freshness_update": "operator_annotation_defer_review_only",
}


class OperatorDecisionCreate(BaseModel):
    """An annotation of one arbiter decision. There is no outcome or execution member to send."""

    model_config = ConfigDict(extra="forbid")

    decision_kind: OperatorAnnotationKind
    reason_codes: list[str] = Field(default_factory=list, max_length=10)


class ReviewManifestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_export_bundle: dict[str, Any]
    retention_class: Literal["short_lived_internal_review", "standard_internal_review", "legal_hold_candidate"] = (
        "standard_internal_review"
    )
    generated_at: AwareDatetime | None = None


class RetentionDispositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retention_disposition_dry_run: dict[str, Any]
    operator_review_packet: dict[str, Any]
    decision_kind: Literal[
        "approve_future_retention_review",
        "approve_future_redaction_review",
        "approve_future_legal_hold_review",
        "request_more_evidence",
        "reject_disposition",
        "defer_until_recheck",
        "block_unsafe_disposition",
    ]
    decided_at: AwareDatetime | None = None


def _tenant_uuid(tenant_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(tenant_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="ledger_tenant_invalid") from exc


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return None if value is None else str(value)


def _reviewer_ref(request: Request, tenant_id: str) -> str:
    """An opaque, stable reference for the authenticated operator (never the raw subject)."""
    state = request.state
    actor = getattr(state, "user_sub", None) or getattr(state, "api_key_name", None) or "unknown"
    return "operator_ref_" + hashlib.sha256(f"{tenant_id}|{actor}".encode()).hexdigest()[:24]


def _require_same_tenant(document: dict[str, Any], tenant_id: str, field: str) -> None:
    if str(document.get("tenant_id") or "") != str(tenant_id):
        raise HTTPException(status_code=403, detail=f"{field} names another tenant")


def decision_view(row: Any) -> dict[str, Any]:
    return {
        "decision_id": row.decision_id,
        "merchant_id": row.merchant_id,
        "seller_agent_id": row.seller_agent_id,
        "buyer_agent_id": row.buyer_agent_id,
        "action": row.action,
        "outcome": row.outcome,
        "rules_evaluated": list(row.rules_evaluated or []),
        "failing_rule": row.failing_rule,
        "rule_results": list(row.rule_results or []),
        "evidence": dict(row.evidence or {}),
        "evidence_hashes": list(row.evidence_hashes or []),
        "payload_hash": row.payload_hash,
        "policy_version": row.policy_version,
        "decided_at": _iso(row.decided_at),
        "expires_at": _iso(row.expires_at),
        "consumed_at": _iso(row.consumed_at),
        "grantex_grant_jti": row.grantex_grant_jti,
        "grantex_recorded_at": _iso(row.grantex_recorded_at),
    }


async def _decision_or_404(session: Any, tenant: uuid.UUID, decision_id: str) -> Any:
    row = await get_decision(session, tenant, decision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    return row


def build_operator_annotation(
    decision_row: Any,
    *,
    tenant_id: str,
    decision_kind: str,
    reason_codes: list[str],
    reviewer_ref: str,
    decided_at: datetime,
) -> dict[str, Any]:
    """The ``oacp_operator_decision_records`` record annotating one arbiter decision (never executable)."""
    scope: dict[str, dict[str, int]] = {
        "tenant": {str(tenant_id): 1},
        "merchant": {decision_row.merchant_id: 1},
        "seller_agent": {decision_row.seller_agent_id: 1},
        "buyer_agent": {decision_row.buyer_agent_id: 1} if decision_row.buyer_agent_id else {},
    }
    decided = _iso(decided_at)
    identity = {
        "arbiter_decision_id": decision_row.decision_id,
        "decision_kind": decision_kind,
        "reviewer_identity_ref": reviewer_ref,
        "decided_at": decided,
    }
    codes = sorted({code.strip().lower()[:64] for code in reason_codes if code.strip()})
    outcome_code = decision_row.failing_rule or "arbiter_allow"
    return {
        "decision_record_id": "oacp_arbiter_annotation_" + commerce_payload_hash(identity)[:32],
        "decision_kind": decision_kind,
        "review_packet_id": decision_row.decision_id,
        "maintenance_plan_id": f"arbiter_decision:{decision_row.decision_id}",
        "generated_at": _iso(decision_row.decided_at),
        "decided_at": decided,
        "status": "recorded_for_future_review",
        "scope_summary": scope,
        "artifact_families_affected": [],
        "artifact_family_counts": {},
        "redacted_reason_codes": {outcome_code: 1, **dict.fromkeys(codes, 1)},
        "source_refs": [f"arbiter_decision:{decision_row.decision_id}"],
        "evidence_refs": [f"arbiter_payload_hash:{decision_row.payload_hash}"],
        "reviewer_identity_ref": reviewer_ref,
        "next_step_labels": [_ANNOTATION_LABELS[decision_kind]],
        "allowed_to_execute": False,
        "no_execution": True,
        "operator_decision_only": True,
        "audit_safe_decision_record": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "raw_payloads_included": False,
        "grantex_runtime_required": False,
    }


def _stored_or_raise(result: dict[str, Any]) -> None:
    if result.get("stored") is True:
        return
    code = str(result.get("refusal_code") or "record_refused")
    raise HTTPException(status_code=409 if code == "record_append_only" else 422, detail=code)


# ---------------------------------------------------------------------------
# Ledger events and arbiter decisions
# ---------------------------------------------------------------------------


@router.get("/events")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.ledger.events.read",
)
async def list_ledger_events(
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The tenant's commerce ledger rows: sealed rows after ``after_seq`` in chain order, then unsealed rows."""
    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        rows = await list_commerce_events(session, tenant, after_seq=after_seq, limit=limit)
    events = [ledger_view(row) for row in rows]
    sealed = [event["chain_seq"] for event in events if event["chain_seq"] is not None]
    return {"events": events, "next_after_seq": max(sealed) if sealed else after_seq}


@router.get("/decisions")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.ledger.decisions.read",
)
async def list_arbiter_decisions(
    action: str | None = Query(default=None, max_length=40),
    outcome: Literal["allow", "refuse"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    from sqlalchemy import select

    from core.models.commerce_arbiter import CommerceArbiterDecisionRow as D

    tenant = _tenant_uuid(tenant_id)
    query = select(D).where(D.tenant_id == tenant)
    if action is not None:
        query = query.where(D.action == action)
    if outcome is not None:
        query = query.where(D.outcome == outcome)
    async with get_tenant_session(tenant) as session:
        rows = (await session.scalars(query.order_by(D.decided_at.desc(), D.decision_id).limit(limit))).all()
    return {"decisions": [decision_view(row) for row in rows]}


@router.get("/decisions/{decision_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.ledger.decision.read",
)
async def read_arbiter_decision(
    decision_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        row = await _decision_or_404(session, tenant, decision_id)
        annotations = await DurableOacpOperatorDecisionRepository(session).list_decisions_for_scope(
            OacpOperatorDecisionRepositoryQuery(tenant_id=str(tenant), review_packet_id=decision_id)
        )
    return {"decision": decision_view(row), "operator_decisions": list(annotations)}


@router.get("/decisions/{decision_id}/operator-decisions")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.ledger.operator_decision.read",
)
async def list_operator_decisions(
    decision_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        await _decision_or_404(session, tenant, decision_id)
        annotations = await DurableOacpOperatorDecisionRepository(session).list_decisions_for_scope(
            OacpOperatorDecisionRepositoryQuery(tenant_id=str(tenant), review_packet_id=decision_id)
        )
    return {"decision_id": decision_id, "operator_decisions": list(annotations)}


@router.post("/decisions/{decision_id}/operator-decisions", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.operator_decisions.write",
    rate_limit="commerce-runtime-write",
    idempotency="arbiter-decision-kind-reviewer-time",
    audit_event="commerce.runtime.ledger.operator_decision.create",
)
async def create_operator_decision(
    decision_id: str,
    body: OperatorDecisionCreate,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Record an operator annotation bound to an arbiter decision. The decision itself is unchanged."""
    tenant = _tenant_uuid(tenant_id)
    now = datetime.now(UTC)
    async with get_tenant_session(tenant) as session:
        row = await _decision_or_404(session, tenant, decision_id)
        record = build_operator_annotation(
            row,
            tenant_id=str(tenant),
            decision_kind=body.decision_kind,
            reason_codes=body.reason_codes,
            reviewer_ref=_reviewer_ref(request, str(tenant)),
            decided_at=now,
        )
        _stored_or_raise(await DurableOacpOperatorDecisionRepository(session).upsert_decision(record))
        await record_commerce_event(
            session,
            tenant_id=tenant,
            event="commerce.runtime.ledger.operator_decision.create",
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=row.buyer_agent_id,
            payload=record,
            resource_id=record["decision_record_id"],
            details={
                "arbiter_decision_id": decision_id,
                "operator_decision_id": record["decision_record_id"],
                "decision_kind": body.decision_kind,
                "arbiter_outcome": row.outcome,
                "outcome_changed": False,
            },
            now=now,
        )
        view = decision_view(row)
    return {
        "operator_decision": record,
        "arbiter_decision": view,
        "outcome_binding": False,
        "allowed_to_execute": False,
    }


# ---------------------------------------------------------------------------
# Review manifests
# ---------------------------------------------------------------------------


@router.post("/manifests", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.manifests.write",
    rate_limit="commerce-runtime-write",
    idempotency="bundle-generated-at-retention-class",
    audit_event="commerce.runtime.ledger.manifest.create",
)
async def create_review_manifest(
    body: ReviewManifestCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Store a review manifest over an OACP audit export bundle of this tenant."""
    tenant = _tenant_uuid(tenant_id)
    _require_same_tenant(body.audit_export_bundle, str(tenant), "audit_export_bundle")
    generated_at = _iso(body.generated_at or datetime.now(UTC)) or ""
    manifest = build_oacp_c6y1_audit_export_review_manifest(
        audit_export_bundle=body.audit_export_bundle,
        generated_at=generated_at,
        retention_class=body.retention_class,
    )
    if manifest.get("status") == "blocked":
        raise HTTPException(status_code=422, detail=str(manifest.get("block_reason") or "manifest_blocked"))
    async with get_tenant_session(tenant) as session:
        _stored_or_raise(await DurableOacpAuditReviewManifestRepository(session).upsert_manifest(manifest))
        await record_commerce_event(
            session,
            tenant_id=tenant,
            event="commerce.runtime.ledger.manifest.create",
            outcome="success",
            merchant_id=manifest.get("merchant_id"),
            seller_agent_id=manifest.get("seller_agent_id"),
            buyer_agent_id=manifest.get("buyer_agent_id"),
            payload=manifest,
            resource_id=str(manifest["manifest_id"]),
            details={"manifest_id": manifest["manifest_id"], "retention_class": body.retention_class},
        )
    return {"manifest": manifest, "allowed_to_execute": False}


@router.get("/manifests")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.ledger.manifests.read",
)
async def list_review_manifests(
    merchant_id: str | None = Query(default=None, max_length=160),
    retention_class: str | None = Query(default=None, max_length=64),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        manifests = await DurableOacpAuditReviewManifestRepository(session).list_manifests_for_scope(
            OacpAuditReviewManifestRepositoryQuery(
                tenant_id=str(tenant),
                merchant_id=merchant_id,
                retention_class=retention_class,  # type: ignore[arg-type]
            )
        )
    return {"manifests": list(manifests)}


# ---------------------------------------------------------------------------
# Export (offline-verifiable bundle)
# ---------------------------------------------------------------------------


@router.get("/export")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.ledger.export.read",
)
async def export_ledger(
    from_seq: int = Query(default=1, ge=1),
    to_seq: int | None = Query(default=None, ge=1),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """A self-contained JSON bundle that ``scripts/verify_ledger.py`` verifies with no database and no network."""
    from core.commerce.oacp_grantex_trust import OacpTrustUnavailableError, default_oacp_grantex_verifier

    tenant = _tenant_uuid(tenant_id)
    verifier = default_oacp_grantex_verifier()
    async with get_tenant_session(tenant) as session:
        bundle = await build_export_bundle(
            session, tenant, from_seq=from_seq, to_seq=to_seq, grantex_issuer=verifier.config.issuer
        )
    witnessed = [w for w in bundle["anchors"]["witness_checkpoints"] if w["status"] == "witnessed"]
    if witnessed:
        # Outside the transaction: the pinned JWKS lets the countersignatures verify offline.
        try:
            bundle["grantex"]["jwks"] = dict(await verifier.jwks())
        except OacpTrustUnavailableError:
            bundle["grantex"]["jwks"] = None
    return bundle


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


@router.post("/retention/dispositions", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.retention.write",
    rate_limit="commerce-runtime-write",
    idempotency="dry-run-packet-kind-reviewer-time",
    audit_event="commerce.runtime.ledger.retention.disposition.create",
)
async def create_retention_disposition(
    body: RetentionDispositionCreate,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Record a retention disposition decision. The retention task applies due ones; nothing is deleted here."""
    tenant = _tenant_uuid(tenant_id)
    _require_same_tenant(body.retention_disposition_dry_run, str(tenant), "retention_disposition_dry_run")
    _require_same_tenant(body.operator_review_packet, str(tenant), "operator_review_packet")
    record = build_oacp_c6y5_retention_disposition_decision_record(
        retention_disposition_dry_run=body.retention_disposition_dry_run,
        operator_review_packet=body.operator_review_packet,
        decision_kind=body.decision_kind,
        reviewer_identity_ref=_reviewer_ref(request, str(tenant)),
        decided_at=_iso(body.decided_at or datetime.now(UTC)) or "",
    )
    if record.get("decision_record_built") is not True:
        raise HTTPException(status_code=422, detail=str(record.get("refusal_code") or "disposition_refused"))
    async with get_tenant_session(tenant) as session:
        _stored_or_raise(
            await DurableOacpRetentionDispositionDecisionRepository(session).upsert_disposition_decision(record)
        )
        await record_commerce_event(
            session,
            tenant_id=tenant,
            event="commerce.runtime.ledger.retention.disposition.create",
            outcome="success",
            merchant_id=record.get("merchant_id"),
            seller_agent_id=record.get("seller_agent_id"),
            buyer_agent_id=record.get("buyer_agent_id"),
            payload=record,
            resource_id=str(record["disposition_decision_id"]),
            details={
                "disposition_decision_id": record["disposition_decision_id"],
                "decision_kind": body.decision_kind,
            },
        )
    return {"disposition_decision": record, "records_deleted": False}


@router.get("/retention/dispositions")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.ledger.retention.read",
)
async def list_retention_dispositions(
    merchant_id: str | None = Query(default=None, max_length=160),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        decisions = await DurableOacpRetentionDispositionDecisionRepository(
            session
        ).list_disposition_decisions_for_scope(
            OacpRetentionDispositionDecisionRepositoryQuery(tenant_id=str(tenant), merchant_id=merchant_id)
        )
    return {"disposition_decisions": list(decisions)}


@router.get("/retention/due")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ledger.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.ledger.retention.due.read",
)
async def list_due_retention(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """What the retention task would delete now: due dispositions and the manifests each makes due."""
    tenant = _tenant_uuid(tenant_id)
    now = datetime.now(UTC)
    async with get_tenant_session(tenant) as session:
        due = []
        for disposition in await due_dispositions(session, tenant, now):
            manifests = await due_manifests(session, disposition, now)
            due.append(
                {
                    "disposition_decision_id": disposition.disposition_decision_id,
                    "retain_until": _iso(disposition.retain_until),
                    "manifest_ids": [manifest.manifest_id for manifest in manifests],
                }
            )
    return {"as_of": _iso(now), "due": due}
