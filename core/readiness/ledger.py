"""Async repository for the capability readiness and evidence ledger."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.claims.schema import ClaimTreatment, GateResult, InternalMaturity, PublicAvailability
from core.models.capability_readiness import (
    CapabilityEvidenceRecord,
    CapabilityPromotionEvent,
    CapabilityReadinessRecord,
)
from core.models.company import Company
from core.models.user import User
from core.readiness.contracts import (
    CLAIM_STATE_RANK,
    INTERNAL_MATURITY_RANK,
    LEDGER_POLICY_VERSION,
    PUBLIC_AVAILABILITY_RANK,
    CapabilityRegistration,
    CurrentReadinessState,
    EvidenceEnvironment,
    EvidenceFact,
    EvidenceRegistration,
    EvidenceTrustState,
    OwnerRotation,
    ReadinessTransition,
    ReviewRenewal,
    ScopeDisposition,
)
from core.readiness.policy import (
    ReadinessValidationError,
    validate_evidence_registration,
    validate_owner_rotation,
    validate_promotion,
    validate_registration,
    validate_review_renewal,
)


class ReadinessNotFoundError(LookupError):
    pass


class ReadinessConflictError(RuntimeError):
    pass


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Unsupported event value: {type(value).__name__}")


def compute_event_hash(previous_event_hash: str | None, payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(f"{previous_event_hash or ''}:{canonical}".encode()).hexdigest()


def _scope_clause(model: type, company_id: UUID | None):
    return model.company_id.is_(None) if company_id is None else model.company_id == company_id


def _initial_gate_results(required_gate_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        gate_id: {
            "status": GateResult.NOT_ASSESSED.value,
            "evidence_ids": [],
            "reviewed_by": None,
            "reviewed_at": None,
            "reason": None,
        }
        for gate_id in required_gate_ids
    }


def _ownership_snapshot(record: CapabilityReadinessRecord) -> dict[str, object]:
    return {
        "owners": dict(sorted(record.owners.items())),
        "approver_ids": sorted(record.approver_ids),
        "review_expires_at": record.review_expires_at.isoformat(),
    }


def _event_payload(event: CapabilityPromotionEvent) -> dict[str, object]:
    return {
        "readiness_record_id": event.readiness_record_id,
        "tenant_id": event.tenant_id,
        "company_id": event.company_id,
        "capability_id": event.capability_id,
        "sequence": event.sequence,
        "event_type": event.event_type,
        "from_internal_maturity": event.from_internal_maturity,
        "to_internal_maturity": event.to_internal_maturity,
        "from_release_gate": event.from_release_gate,
        "to_release_gate": event.to_release_gate,
        "from_public_availability": event.from_public_availability,
        "to_public_availability": event.to_public_availability,
        "from_claim_state": event.from_claim_state,
        "to_claim_state": event.to_claim_state,
        "scope_disposition_snapshot": event.scope_disposition_snapshot,
        "gate_results_snapshot": event.gate_results_snapshot,
        "evidence_snapshot": event.evidence_snapshot,
        "ownership_snapshot": event.ownership_snapshot,
        "traceability_snapshot": event.traceability_snapshot,
        "permitted_claim_ids": event.permitted_claim_ids,
        "limitations": event.limitations,
        "requested_by": event.requested_by,
        "approved_by": event.approved_by,
        "approval_reference": event.approval_reference,
        "reason": event.reason,
        "policy_version": event.policy_version,
    }


def verify_event_chain(events: list[CapabilityPromotionEvent]) -> str | None:
    """Fail closed unless ordered persisted events form the canonical chain."""
    previous_hash: str | None = None
    scope: tuple[object, object, object, object] | None = None
    for expected_sequence, event in enumerate(events):
        event_scope = (
            event.readiness_record_id,
            event.tenant_id,
            event.company_id,
            event.capability_id,
        )
        if scope is None:
            scope = event_scope
        if event_scope != scope:
            raise ReadinessConflictError("Promotion history crosses capability scope")
        if event.sequence != expected_sequence:
            raise ReadinessConflictError("Promotion history sequence is not contiguous")
        if event.previous_event_hash != previous_hash:
            raise ReadinessConflictError("Promotion history predecessor hash does not match")
        expected_hash = compute_event_hash(previous_hash, _event_payload(event))
        if not hmac.compare_digest(event.event_hash, expected_hash):
            raise ReadinessConflictError("Promotion history event hash is invalid")
        previous_hash = event.event_hash
    return previous_hash


def _gate_rank(value: GateResult) -> int:
    return {
        GateResult.EXPIRED: -1,
        GateResult.BLOCKED: 0,
        GateResult.NOT_ASSESSED: 1,
        GateResult.IN_REVIEW: 2,
        GateResult.PASSED: 3,
    }[value]


def _transition_kind(record: CapabilityReadinessRecord, transition: ReadinessTransition) -> str:
    old = (
        INTERNAL_MATURITY_RANK[InternalMaturity(record.internal_maturity_state)],
        PUBLIC_AVAILABILITY_RANK[PublicAvailability(record.public_availability_state)],
        CLAIM_STATE_RANK[ClaimTreatment(record.claim_state)],
        _gate_rank(GateResult(record.release_gate_state)),
    )
    new = (
        INTERNAL_MATURITY_RANK[transition.target_internal_maturity],
        PUBLIC_AVAILABILITY_RANK[transition.target_public_availability],
        CLAIM_STATE_RANK[transition.target_claim_state],
        _gate_rank(transition.target_release_gate),
    )
    if any(after > before for before, after in zip(old, new, strict=True)):
        return "promoted"
    if any(after < before for before, after in zip(old, new, strict=True)):
        return "demoted"
    return "attested"


def _is_upward_transition(record: CapabilityReadinessRecord, transition: ReadinessTransition) -> bool:
    if _transition_kind(record, transition) == "promoted":
        return True
    if not set(transition.permitted_claim_ids).issubset(record.permitted_claim_ids):
        return True
    return not set(record.limitations).issubset(transition.limitations)


class CapabilityReadinessLedger:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _assert_company_scope(self, tenant_id: UUID, company_id: UUID | None) -> None:
        if company_id is None:
            return
        result = await self.session.execute(
            select(Company.id).where(Company.tenant_id == tenant_id, Company.id == company_id)
        )
        if result.scalar_one_or_none() is None:
            raise ReadinessNotFoundError("Company not found in tenant scope")

    async def _assert_active_users(self, tenant_id: UUID, actor_ids: set[str]) -> None:
        parsed: set[UUID] = set()
        invalid: list[str] = []
        for actor_id in actor_ids:
            try:
                parsed.add(UUID(actor_id))
            except (TypeError, ValueError):
                invalid.append(actor_id)
        if invalid:
            raise ReadinessValidationError(["owners and approvers must be local user IDs"])
        result = await self.session.execute(
            select(User.id).where(
                User.tenant_id == tenant_id,
                User.id.in_(parsed),
                User.status == "active",
            )
        )
        found = set(result.scalars().all())
        if found != parsed:
            raise ReadinessValidationError(["every owner and approver must be an active same-tenant user"])

    async def _load_history(self, record: CapabilityReadinessRecord) -> list[CapabilityPromotionEvent]:
        result = await self.session.execute(
            select(CapabilityPromotionEvent)
            .where(CapabilityPromotionEvent.readiness_record_id == record.id)
            .order_by(CapabilityPromotionEvent.sequence)
        )
        history = list(result.scalars().all())
        if not history:
            raise ReadinessConflictError("Capability registration history is missing")
        verify_event_chain(history)
        if history[-1].sequence != record.current_promotion_sequence:
            raise ReadinessConflictError("Readiness history does not match current record sequence")
        return history

    async def get_capability(
        self,
        tenant_id: UUID,
        company_id: UUID | None,
        capability_id: str,
        *,
        for_update: bool = False,
    ) -> CapabilityReadinessRecord:
        statement = select(CapabilityReadinessRecord).where(
            CapabilityReadinessRecord.tenant_id == tenant_id,
            CapabilityReadinessRecord.capability_id == capability_id,
            _scope_clause(CapabilityReadinessRecord, company_id),
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        record = result.scalar_one_or_none()
        if record is None:
            raise ReadinessNotFoundError("Capability not found in tenant/company scope")
        return record

    async def get_history(
        self,
        tenant_id: UUID,
        company_id: UUID | None,
        capability_id: str,
    ) -> list[CapabilityPromotionEvent]:
        record = await self.get_capability(tenant_id, company_id, capability_id)
        return await self._load_history(record)

    async def register_capability(
        self,
        *,
        tenant_id: UUID,
        company_id: UUID | None,
        registration: CapabilityRegistration,
        actor_id: str,
        now: datetime | None = None,
    ) -> CapabilityReadinessRecord:
        clock = now or datetime.now(UTC)
        validate_registration(registration, now=clock)
        await self._assert_company_scope(tenant_id, company_id)
        await self._assert_active_users(
            tenant_id,
            set(registration.owners.values()).union(registration.approver_ids),
        )
        existing = await self.session.execute(
            select(CapabilityReadinessRecord.id).where(
                CapabilityReadinessRecord.tenant_id == tenant_id,
                CapabilityReadinessRecord.capability_id == registration.capability_id,
                _scope_clause(CapabilityReadinessRecord, company_id),
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ReadinessConflictError("Capability already exists in tenant/company scope")
        gate_results = _initial_gate_results(registration.required_gate_ids)
        record = CapabilityReadinessRecord(
            tenant_id=tenant_id,
            company_id=company_id,
            capability_id=registration.capability_id,
            domain=registration.domain,
            title=registration.title,
            description=registration.description,
            scope_disposition=registration.scope_disposition.value,
            scope_condition=registration.scope_condition,
            scope_details=registration.scope_details,
            required_gate_ids=list(registration.required_gate_ids),
            gate_results=gate_results,
            owners=registration.owners,
            approver_ids=list(registration.approver_ids),
            traceability={key: list(value) for key, value in registration.traceability.items()},
            limitations=list(registration.limitations),
            feature_flag=registration.feature_flag,
            review_expires_at=registration.review_expires_at,
            created_by=actor_id,
            updated_by=actor_id,
        )
        try:
            self.session.add(record)
            await self.session.flush()
            event = CapabilityPromotionEvent(
                readiness_record_id=record.id,
                tenant_id=tenant_id,
                company_id=company_id,
                capability_id=record.capability_id,
                sequence=0,
                event_type="registered",
                from_internal_maturity=None,
                to_internal_maturity=record.internal_maturity_state,
                from_release_gate=None,
                to_release_gate=record.release_gate_state,
                from_public_availability=None,
                to_public_availability=record.public_availability_state,
                from_claim_state=None,
                to_claim_state=record.claim_state,
                scope_disposition_snapshot=record.scope_disposition,
                gate_results_snapshot=gate_results,
                evidence_snapshot=[],
                ownership_snapshot=_ownership_snapshot(record),
                traceability_snapshot=record.traceability,
                permitted_claim_ids=[],
                limitations=record.limitations,
                requested_by=actor_id,
                approved_by=actor_id,
                approval_reference=None,
                reason="Capability registered",
                policy_version=LEDGER_POLICY_VERSION,
                previous_event_hash=None,
                event_hash="",
            )
            event.event_hash = compute_event_hash(None, _event_payload(event))
            self.session.add(event)
            await self.session.flush()
        except IntegrityError as exc:
            raise ReadinessConflictError("Capability registration conflicts with persisted ledger state") from exc
        return record

    async def add_evidence(
        self,
        *,
        tenant_id: UUID,
        company_id: UUID | None,
        capability_id: str,
        evidence: EvidenceRegistration,
        actor_id: str,
        now: datetime | None = None,
    ) -> CapabilityEvidenceRecord:
        validate_evidence_registration(evidence, now=now or datetime.now(UTC))
        record = await self.get_capability(tenant_id, company_id, capability_id)
        existing = await self.session.execute(
            select(CapabilityEvidenceRecord.id).where(
                CapabilityEvidenceRecord.readiness_record_id == record.id,
                CapabilityEvidenceRecord.evidence_version == evidence.evidence_version,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ReadinessConflictError("Evidence version already exists for capability")
        row = CapabilityEvidenceRecord(
            readiness_record_id=record.id,
            tenant_id=tenant_id,
            company_id=company_id,
            capability_id=capability_id,
            evidence_version=evidence.evidence_version,
            evidence_type=evidence.evidence_type,
            artifact_uri=evidence.artifact_uri,
            sha256_checksum=evidence.sha256_checksum,
            environment=evidence.environment.value,
            provider_account_class=evidence.provider_account_class,
            product_version=evidence.product_version,
            source_commit_sha=evidence.source_commit_sha,
            trust_state=EvidenceTrustState.UNVERIFIED.value,
            submitted_by=actor_id,
            observed_at=evidence.observed_at,
            reviewed_at=None,
            expires_at=evidence.expires_at,
            reviewed_by=None,
            supports_gate_ids=list(evidence.supports_gate_ids),
            supports_claim_ids=list(evidence.supports_claim_ids),
            evidence_metadata=evidence.metadata,
            created_by=actor_id,
        )
        try:
            self.session.add(row)
            await self.session.flush()
        except IntegrityError as exc:
            raise ReadinessConflictError("Evidence version conflicts with persisted ledger state") from exc
        return row

    async def _append_governance_event(
        self,
        *,
        record: CapabilityReadinessRecord,
        event_type: str,
        actor_id: str,
        reason: str,
        ownership_snapshot: dict[str, object],
    ) -> CapabilityPromotionEvent:
        history = await self._load_history(record)
        previous = history[-1]
        event = CapabilityPromotionEvent(
            readiness_record_id=record.id,
            tenant_id=record.tenant_id,
            company_id=record.company_id,
            capability_id=record.capability_id,
            sequence=record.current_promotion_sequence + 1,
            event_type=event_type,
            from_internal_maturity=record.internal_maturity_state,
            to_internal_maturity=record.internal_maturity_state,
            from_release_gate=record.release_gate_state,
            to_release_gate=record.release_gate_state,
            from_public_availability=record.public_availability_state,
            to_public_availability=record.public_availability_state,
            from_claim_state=record.claim_state,
            to_claim_state=record.claim_state,
            scope_disposition_snapshot=record.scope_disposition,
            gate_results_snapshot=record.gate_results,
            evidence_snapshot=previous.evidence_snapshot,
            ownership_snapshot=ownership_snapshot,
            traceability_snapshot=record.traceability,
            permitted_claim_ids=record.permitted_claim_ids,
            limitations=record.limitations,
            requested_by=actor_id,
            approved_by=actor_id,
            approval_reference=None,
            reason=reason,
            policy_version=LEDGER_POLICY_VERSION,
            previous_event_hash=previous.event_hash,
            event_hash="",
        )
        event.event_hash = compute_event_hash(previous.event_hash, _event_payload(event))
        try:
            self.session.add(event)
            await self.session.flush()
        except IntegrityError as exc:
            raise ReadinessConflictError("Governance event conflicts with persisted ledger state") from exc
        return event

    async def transition_capability(
        self,
        *,
        tenant_id: UUID,
        company_id: UUID | None,
        capability_id: str,
        transition: ReadinessTransition,
        requested_by: str,
        now: datetime | None = None,
    ) -> CapabilityPromotionEvent:
        clock = now or datetime.now(UTC)
        record = await self.get_capability(tenant_id, company_id, capability_id, for_update=True)
        if _is_upward_transition(record, transition):
            raise ReadinessConflictError(
                "Upward readiness promotion is disabled until v2 evidence attestations are implemented"
            )
        rows: list[CapabilityEvidenceRecord] = []
        if transition.evidence_ids:
            result = await self.session.execute(
                select(CapabilityEvidenceRecord).where(
                    CapabilityEvidenceRecord.readiness_record_id == record.id,
                    CapabilityEvidenceRecord.tenant_id == tenant_id,
                    _scope_clause(CapabilityEvidenceRecord, company_id),
                    CapabilityEvidenceRecord.id.in_(transition.evidence_ids),
                )
            )
            rows = list(result.scalars().all())
        facts = tuple(
            EvidenceFact(
                evidence_id=row.id,
                tenant_id=row.tenant_id,
                company_id=row.company_id,
                capability_id=row.capability_id,
                evidence_version=row.evidence_version,
                evidence_type=row.evidence_type,
                artifact_uri=row.artifact_uri,
                sha256_checksum=row.sha256_checksum,
                environment=EvidenceEnvironment(row.environment),
                provider_account_class=row.provider_account_class,
                product_version=row.product_version,
                source_commit_sha=row.source_commit_sha,
                trust_state=EvidenceTrustState(row.trust_state),
                submitted_by=row.submitted_by,
                reviewed_at=row.reviewed_at,
                expires_at=row.expires_at,
                reviewed_by=row.reviewed_by,
                supports_gate_ids=tuple(row.supports_gate_ids),
                supports_claim_ids=tuple(row.supports_claim_ids),
            )
            for row in rows
        )
        current = CurrentReadinessState(
            internal_maturity=InternalMaturity(record.internal_maturity_state),
            release_gate=GateResult(record.release_gate_state),
            public_availability=PublicAvailability(record.public_availability_state),
            claim_state=ClaimTreatment(record.claim_state),
            gate_results=record.gate_results,
            permitted_claim_ids=tuple(record.permitted_claim_ids),
        )
        decision = validate_promotion(
            tenant_id=tenant_id,
            company_id=company_id,
            capability_id=capability_id,
            scope_disposition=ScopeDisposition(record.scope_disposition),
            required_gate_ids=tuple(record.required_gate_ids),
            approver_ids=tuple(record.approver_ids),
            current=current,
            transition=transition,
            evidence=facts,
            requested_by=requested_by,
            review_expires_at=record.review_expires_at,
            now=clock,
        )
        history = await self._load_history(record)
        previous = history[-1]
        sequence = record.current_promotion_sequence + 1
        event = CapabilityPromotionEvent(
            readiness_record_id=record.id,
            tenant_id=tenant_id,
            company_id=company_id,
            capability_id=capability_id,
            sequence=sequence,
            event_type=_transition_kind(record, transition),
            from_internal_maturity=record.internal_maturity_state,
            to_internal_maturity=transition.target_internal_maturity.value,
            from_release_gate=record.release_gate_state,
            to_release_gate=decision.release_gate.value,
            from_public_availability=record.public_availability_state,
            to_public_availability=transition.target_public_availability.value,
            from_claim_state=record.claim_state,
            to_claim_state=transition.target_claim_state.value,
            scope_disposition_snapshot=record.scope_disposition,
            gate_results_snapshot=decision.gate_results,
            evidence_snapshot=list(decision.evidence_snapshot),
            ownership_snapshot=_ownership_snapshot(record),
            traceability_snapshot=record.traceability,
            permitted_claim_ids=list(transition.permitted_claim_ids),
            limitations=list(transition.limitations),
            requested_by=requested_by,
            approved_by=transition.approved_by,
            approval_reference=transition.approval_reference,
            reason=transition.reason,
            policy_version=LEDGER_POLICY_VERSION,
            previous_event_hash=previous.event_hash,
            event_hash="",
        )
        event.event_hash = compute_event_hash(previous.event_hash, _event_payload(event))
        try:
            self.session.add(event)
            await self.session.flush()
            record.internal_maturity_state = transition.target_internal_maturity.value
            record.release_gate_state = decision.release_gate.value
            record.public_availability_state = transition.target_public_availability.value
            record.claim_state = transition.target_claim_state.value
            record.gate_results = decision.gate_results
            record.permitted_claim_ids = list(transition.permitted_claim_ids)
            record.limitations = list(transition.limitations)
            record.current_promotion_sequence = sequence
            record.updated_by = requested_by
            record.updated_at = clock
            await self.session.flush()
        except IntegrityError as exc:
            raise ReadinessConflictError("Promotion conflicts with persisted ledger state") from exc
        return event

    async def renew_review(
        self,
        *,
        tenant_id: UUID,
        company_id: UUID | None,
        capability_id: str,
        renewal: ReviewRenewal,
        actor_id: str,
        now: datetime | None = None,
    ) -> CapabilityPromotionEvent:
        clock = now or datetime.now(UTC)
        record = await self.get_capability(tenant_id, company_id, capability_id, for_update=True)
        if renewal.expected_sequence != record.current_promotion_sequence:
            raise ReadinessConflictError("Readiness sequence changed before review renewal")
        expires_at = validate_review_renewal(
            renewal,
            current_expires_at=record.review_expires_at,
            now=clock,
        )
        ownership_snapshot = {
            "owners": dict(sorted(record.owners.items())),
            "approver_ids": sorted(record.approver_ids),
            "review_expires_at": expires_at.isoformat(),
        }
        event = await self._append_governance_event(
            record=record,
            event_type="review_renewed",
            actor_id=actor_id,
            reason=renewal.reason,
            ownership_snapshot=ownership_snapshot,
        )
        record.review_expires_at = expires_at
        record.current_promotion_sequence = event.sequence
        record.updated_by = actor_id
        record.updated_at = clock
        await self.session.flush()
        return event

    async def rotate_owners(
        self,
        *,
        tenant_id: UUID,
        company_id: UUID | None,
        capability_id: str,
        rotation: OwnerRotation,
        actor_id: str,
        now: datetime | None = None,
    ) -> CapabilityPromotionEvent:
        clock = now or datetime.now(UTC)
        record = await self.get_capability(tenant_id, company_id, capability_id, for_update=True)
        if rotation.expected_sequence != record.current_promotion_sequence:
            raise ReadinessConflictError("Readiness sequence changed before owner rotation")
        validate_owner_rotation(
            rotation,
            current_owners=record.owners,
            current_approver_ids=tuple(record.approver_ids),
        )
        await self._assert_active_users(
            tenant_id,
            set(rotation.owners.values()).union(rotation.approver_ids),
        )
        ownership_snapshot = {
            "owners": dict(sorted(rotation.owners.items())),
            "approver_ids": sorted(rotation.approver_ids),
            "review_expires_at": record.review_expires_at.isoformat(),
        }
        event = await self._append_governance_event(
            record=record,
            event_type="owners_rotated",
            actor_id=actor_id,
            reason=rotation.reason,
            ownership_snapshot=ownership_snapshot,
        )
        record.owners = rotation.owners
        record.approver_ids = list(rotation.approver_ids)
        record.current_promotion_sequence = event.sequence
        record.updated_by = actor_id
        record.updated_at = clock
        await self.session.flush()
        return event
