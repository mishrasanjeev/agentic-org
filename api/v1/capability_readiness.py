"""Admin API for tenant/company capability readiness and evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.deps import ActiveHumanAdmin, get_active_human_admin, get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.claims.schema import ClaimTreatment, GateResult, InternalMaturity, PublicAvailability
from core.database import get_tenant_session
from core.models.capability_readiness import CapabilityPromotionEvent, CapabilityReadinessRecord
from core.readiness.contracts import (
    CapabilityRegistration,
    EvidenceEnvironment,
    EvidenceRegistration,
    GateAttestation,
    OwnerRotation,
    ReadinessTransition,
    ReviewRenewal,
    ScopeDisposition,
)
from core.readiness.ledger import (
    CapabilityReadinessLedger,
    ReadinessConflictError,
    ReadinessNotFoundError,
)
from core.readiness.policy import ReadinessValidationError

router = APIRouter(
    prefix="/capability-readiness",
    dependencies=[require_tenant_admin],
)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityRegistrationBody(StrictBody):
    company_id: UUID | None = None
    capability_id: str
    domain: str
    title: str
    description: str
    scope_disposition: ScopeDisposition
    scope_condition: str | None = None
    scope_details: dict[str, object] = Field(default_factory=dict)
    required_gate_ids: list[str]
    owners: dict[str, UUID]
    approver_ids: list[UUID]
    traceability: dict[str, list[str]]
    limitations: list[str]
    feature_flag: str | None = None
    review_expires_at: datetime

    def contract(self) -> CapabilityRegistration:
        return CapabilityRegistration(
            capability_id=self.capability_id,
            domain=self.domain,
            title=self.title,
            description=self.description,
            scope_disposition=self.scope_disposition,
            scope_condition=self.scope_condition,
            scope_details=self.scope_details,
            required_gate_ids=tuple(self.required_gate_ids),
            owners={role: str(user_id) for role, user_id in self.owners.items()},
            approver_ids=tuple(str(user_id) for user_id in self.approver_ids),
            traceability={key: tuple(value) for key, value in self.traceability.items()},
            limitations=tuple(self.limitations),
            feature_flag=self.feature_flag,
            review_expires_at=self.review_expires_at,
        )


class EvidenceRegistrationBody(StrictBody):
    company_id: UUID | None = None
    evidence_version: str
    evidence_type: str
    artifact_uri: str
    sha256_checksum: str
    environment: EvidenceEnvironment
    provider_account_class: str
    product_version: str
    source_commit_sha: str
    observed_at: datetime
    expires_at: datetime
    supports_gate_ids: list[str] = Field(default_factory=list)
    supports_claim_ids: list[str] = Field(default_factory=list)
    evidence_metadata: dict[str, object] = Field(default_factory=dict)

    def contract(self) -> EvidenceRegistration:
        return EvidenceRegistration(
            evidence_version=self.evidence_version,
            evidence_type=self.evidence_type,
            artifact_uri=self.artifact_uri,
            sha256_checksum=self.sha256_checksum,
            environment=self.environment,
            provider_account_class=self.provider_account_class,
            product_version=self.product_version,
            source_commit_sha=self.source_commit_sha,
            observed_at=self.observed_at,
            expires_at=self.expires_at,
            supports_gate_ids=tuple(self.supports_gate_ids),
            supports_claim_ids=tuple(self.supports_claim_ids),
            metadata=self.evidence_metadata,
        )


class GateAttestationBody(StrictBody):
    gate_id: str
    status: GateResult
    evidence_ids: list[UUID] = Field(default_factory=list)
    reason: str | None = None

    def contract(self, *, actor_id: str, now: datetime) -> GateAttestation:
        reviewed = self.status is GateResult.PASSED
        return GateAttestation(
            gate_id=self.gate_id,
            status=self.status,
            evidence_ids=tuple(self.evidence_ids),
            reviewed_by=actor_id if reviewed else None,
            reviewed_at=now if reviewed else None,
            reason=self.reason,
        )


class ReadinessTransitionBody(StrictBody):
    company_id: UUID | None = None
    target_internal_maturity: InternalMaturity
    target_release_gate: GateResult
    target_public_availability: PublicAvailability
    target_claim_state: ClaimTreatment
    gate_attestations: list[GateAttestationBody]
    evidence_ids: list[UUID] = Field(default_factory=list)
    permitted_claim_ids: list[str] = Field(default_factory=list)
    limitations: list[str]
    reason: str

    def contract(self, *, actor_id: str, now: datetime) -> ReadinessTransition:
        return ReadinessTransition(
            target_internal_maturity=self.target_internal_maturity,
            target_release_gate=self.target_release_gate,
            target_public_availability=self.target_public_availability,
            target_claim_state=self.target_claim_state,
            gate_attestations=tuple(item.contract(actor_id=actor_id, now=now) for item in self.gate_attestations),
            evidence_ids=tuple(self.evidence_ids),
            permitted_claim_ids=tuple(self.permitted_claim_ids),
            limitations=tuple(self.limitations),
            reason=self.reason,
            approved_by=actor_id,
            approval_reference=None,
        )


class ReviewRenewalBody(StrictBody):
    company_id: UUID | None = None
    expected_sequence: int = Field(ge=0)
    valid_for_days: int = Field(ge=1, le=90)
    reason: str = Field(min_length=1, max_length=2000)

    def contract(self) -> ReviewRenewal:
        return ReviewRenewal(
            expected_sequence=self.expected_sequence,
            valid_for_days=self.valid_for_days,
            reason=self.reason,
        )


class OwnerRotationBody(StrictBody):
    company_id: UUID | None = None
    expected_sequence: int = Field(ge=0)
    owners: dict[str, UUID]
    approver_ids: list[UUID]
    reason: str = Field(min_length=1, max_length=2000)

    def contract(self) -> OwnerRotation:
        return OwnerRotation(
            expected_sequence=self.expected_sequence,
            owners={role: str(user_id) for role, user_id in self.owners.items()},
            approver_ids=tuple(str(user_id) for user_id in self.approver_ids),
            reason=self.reason,
        )


def _tenant(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(401, "Invalid tenant context") from exc


def _record_payload(
    record: CapabilityReadinessRecord,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    clock = now or datetime.now(UTC)
    expired = record.review_expires_at.tzinfo is None or record.review_expires_at <= clock
    gate_results = record.gate_results
    if expired:
        gate_results = {
            gate_id: {
                **(details if isinstance(details, dict) else {}),
                "status": GateResult.EXPIRED.value,
                "reason": "Capability governance review expired.",
            }
            for gate_id, details in record.gate_results.items()
        }
    return {
        "id": str(record.id),
        "tenant_id": str(record.tenant_id),
        "company_id": str(record.company_id) if record.company_id else None,
        "capability_id": record.capability_id,
        "domain": record.domain,
        "title": record.title,
        "description": record.description,
        "scope_disposition": record.scope_disposition,
        "scope_condition": record.scope_condition,
        "scope_details": record.scope_details,
        "required_gate_ids": record.required_gate_ids,
        "internal_maturity": record.internal_maturity_state,
        "release_gate": GateResult.EXPIRED.value if expired else record.release_gate_state,
        "public_availability": (PublicAvailability.UNAVAILABLE.value if expired else record.public_availability_state),
        "claim_treatment": ClaimTreatment.HIDDEN.value if expired else record.claim_state,
        "gate_results": gate_results,
        "permitted_claim_ids": [] if expired else record.permitted_claim_ids,
        "owners": record.owners,
        "approver_ids": record.approver_ids,
        "traceability": record.traceability,
        "limitations": record.limitations,
        "feature_flag": record.feature_flag,
        "review_expires_at": record.review_expires_at,
        "promotion_sequence": record.current_promotion_sequence,
        "review_status": "expired" if expired else "current",
        "recorded_state": {
            "internal_maturity": record.internal_maturity_state,
            "release_gate": record.release_gate_state,
            "public_availability": record.public_availability_state,
            "claim_treatment": record.claim_state,
            "permitted_claim_ids": record.permitted_claim_ids,
        },
        "updated_at": record.updated_at,
    }


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ReadinessNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, ReadinessConflictError):
        return HTTPException(409, str(exc))
    if isinstance(exc, ReadinessValidationError):
        return HTTPException(422, {"message": "Readiness policy rejected the request", "problems": exc.problems})
    return HTTPException(500, "Readiness operation failed")


def _event_response(event: CapabilityPromotionEvent) -> dict[str, object]:
    return {
        "event_id": str(event.id),
        "sequence": event.sequence,
        "event_type": event.event_type,
        "event_hash": event.event_hash,
        "previous_event_hash": event.previous_event_hash,
        "to_internal_maturity": event.to_internal_maturity,
        "to_release_gate": event.to_release_gate,
        "to_public_availability": event.to_public_availability,
        "to_claim_state": event.to_claim_state,
    }


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="capability_readiness.governance_sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="capability_readiness.list",
)
async def list_capabilities(
    company_id: Annotated[UUID | None, Query()] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> list[dict[str, object]]:
    tid = _tenant(tenant_id)
    async with get_tenant_session(tid, company_id) as session:
        scope = (
            CapabilityReadinessRecord.company_id.is_(None)
            if company_id is None
            else CapabilityReadinessRecord.company_id == company_id
        )
        result = await session.execute(
            select(CapabilityReadinessRecord)
            .where(CapabilityReadinessRecord.tenant_id == tid, scope)
            .order_by(CapabilityReadinessRecord.capability_id)
        )
        return [_record_payload(record) for record in result.scalars().all()]


@router.get("/{capability_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="capability_readiness.governance_sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="capability_readiness.read",
)
async def get_capability(
    capability_id: str,
    company_id: Annotated[UUID | None, Query()] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, object]:
    tid = _tenant(tenant_id)
    async with get_tenant_session(tid, company_id) as session:
        try:
            record = await CapabilityReadinessLedger(session).get_capability(tid, company_id, capability_id)
        except (ReadinessNotFoundError, ReadinessConflictError, ReadinessValidationError) as exc:
            raise _translate_error(exc) from exc
        return _record_payload(record)


@router.post("", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="capability_readiness.governance_sensitive.write",
    rate_limit="standard",
    idempotency="capability-readiness-register",
    audit_event="capability_readiness.register",
)
async def register_capability(
    body: CapabilityRegistrationBody,
    tenant_id: str = Depends(get_current_tenant),
    principal: ActiveHumanAdmin = Depends(get_active_human_admin),
) -> dict[str, object]:
    tid = _tenant(tenant_id)
    async with get_tenant_session(tid, body.company_id) as session:
        try:
            record = await CapabilityReadinessLedger(session).register_capability(
                tenant_id=tid,
                company_id=body.company_id,
                registration=body.contract(),
                actor_id=str(principal.user_id),
            )
        except (ReadinessNotFoundError, ReadinessConflictError, ReadinessValidationError) as exc:
            raise _translate_error(exc) from exc
        return _record_payload(record)


@router.post("/{capability_id}/evidence", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="capability_readiness.governance_sensitive.write",
    rate_limit="standard",
    idempotency="capability-evidence-version",
    audit_event="capability_readiness.evidence.create",
)
async def add_evidence(
    capability_id: str,
    body: EvidenceRegistrationBody,
    tenant_id: str = Depends(get_current_tenant),
    principal: ActiveHumanAdmin = Depends(get_active_human_admin),
) -> dict[str, object]:
    tid = _tenant(tenant_id)
    async with get_tenant_session(tid, body.company_id) as session:
        try:
            row = await CapabilityReadinessLedger(session).add_evidence(
                tenant_id=tid,
                company_id=body.company_id,
                capability_id=capability_id,
                evidence=body.contract(),
                actor_id=str(principal.user_id),
            )
        except (ReadinessNotFoundError, ReadinessConflictError, ReadinessValidationError) as exc:
            raise _translate_error(exc) from exc
        return {
            "evidence_id": str(row.id),
            "capability_id": row.capability_id,
            "evidence_version": row.evidence_version,
            "sha256_checksum": row.sha256_checksum,
            "environment": row.environment,
            "trust_state": row.trust_state,
            "submitted_by": row.submitted_by,
            "reviewed_at": row.reviewed_at,
            "reviewed_by": row.reviewed_by,
            "expires_at": row.expires_at,
        }


@router.post("/{capability_id}/transitions", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="capability_readiness.governance_sensitive.write",
    rate_limit="standard",
    idempotency="capability-promotion-sequence",
    audit_event="capability_readiness.transition",
)
async def transition_capability(
    capability_id: str,
    body: ReadinessTransitionBody,
    tenant_id: str = Depends(get_current_tenant),
    principal: ActiveHumanAdmin = Depends(get_active_human_admin),
) -> dict[str, object]:
    tid = _tenant(tenant_id)
    clock = datetime.now(UTC)
    async with get_tenant_session(tid, body.company_id) as session:
        try:
            event = await CapabilityReadinessLedger(session).transition_capability(
                tenant_id=tid,
                company_id=body.company_id,
                capability_id=capability_id,
                transition=body.contract(actor_id=str(principal.user_id), now=clock),
                requested_by=str(principal.user_id),
                now=clock,
            )
        except (ReadinessNotFoundError, ReadinessConflictError, ReadinessValidationError) as exc:
            raise _translate_error(exc) from exc
        return _event_response(event)


@router.post("/{capability_id}/review-renewals", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="capability_readiness.governance_sensitive.write",
    rate_limit="standard",
    idempotency="capability-review-sequence",
    audit_event="capability_readiness.review.renew",
)
async def renew_capability_review(
    capability_id: str,
    body: ReviewRenewalBody,
    tenant_id: str = Depends(get_current_tenant),
    principal: ActiveHumanAdmin = Depends(get_active_human_admin),
) -> dict[str, object]:
    tid = _tenant(tenant_id)
    async with get_tenant_session(tid, body.company_id) as session:
        try:
            event = await CapabilityReadinessLedger(session).renew_review(
                tenant_id=tid,
                company_id=body.company_id,
                capability_id=capability_id,
                renewal=body.contract(),
                actor_id=str(principal.user_id),
            )
        except (ReadinessNotFoundError, ReadinessConflictError, ReadinessValidationError) as exc:
            raise _translate_error(exc) from exc
        return _event_response(event)


@router.post("/{capability_id}/owner-rotations", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="capability_readiness.governance_sensitive.write",
    rate_limit="standard",
    idempotency="capability-owner-sequence",
    audit_event="capability_readiness.owners.rotate",
)
async def rotate_capability_owners(
    capability_id: str,
    body: OwnerRotationBody,
    tenant_id: str = Depends(get_current_tenant),
    principal: ActiveHumanAdmin = Depends(get_active_human_admin),
) -> dict[str, object]:
    tid = _tenant(tenant_id)
    async with get_tenant_session(tid, body.company_id) as session:
        try:
            event = await CapabilityReadinessLedger(session).rotate_owners(
                tenant_id=tid,
                company_id=body.company_id,
                capability_id=capability_id,
                rotation=body.contract(),
                actor_id=str(principal.user_id),
            )
        except (ReadinessNotFoundError, ReadinessConflictError, ReadinessValidationError) as exc:
            raise _translate_error(exc) from exc
        return _event_response(event)


@router.get("/{capability_id}/history")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="capability_readiness.governance_sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="capability_readiness.history.read",
)
async def capability_history(
    capability_id: str,
    company_id: Annotated[UUID | None, Query()] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> list[dict[str, object]]:
    tid = _tenant(tenant_id)
    async with get_tenant_session(tid, company_id) as session:
        try:
            events = await CapabilityReadinessLedger(session).get_history(tid, company_id, capability_id)
        except (ReadinessNotFoundError, ReadinessConflictError, ReadinessValidationError) as exc:
            raise _translate_error(exc) from exc
        return [
            {
                "event_id": str(event.id),
                "sequence": event.sequence,
                "event_type": event.event_type,
                "event_hash": event.event_hash,
                "previous_event_hash": event.previous_event_hash,
                "created_at": event.created_at,
            }
            for event in events
        ]
