"""Tenant/company-scoped capability readiness and immutable evidence history."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, TIMESTAMP, CheckConstraint, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.claims.schema import ClaimTreatment, GateResult, InternalMaturity, PublicAvailability
from core.models.base import BaseModel
from core.readiness.contracts import EvidenceEnvironment, EvidenceTrustState, ScopeDisposition

READINESS_JSON = JSONB().with_variant(JSON(), "sqlite")


def _enum_sql(enum_type: type[StrEnum]) -> str:
    return ", ".join(f"'{item.value}'" for item in enum_type)


class CapabilityReadinessRecord(BaseModel):
    __tablename__ = "capability_readiness_records"
    __table_args__ = (
        CheckConstraint(
            f"scope_disposition IN ({_enum_sql(ScopeDisposition)})", name="ck_capability_scope_disposition"
        ),
        CheckConstraint(
            f"internal_maturity_state IN ({_enum_sql(InternalMaturity)})", name="ck_capability_internal_maturity"
        ),
        CheckConstraint(f"release_gate_state IN ({_enum_sql(GateResult)})", name="ck_capability_release_gate"),
        CheckConstraint(
            f"public_availability_state IN ({_enum_sql(PublicAvailability)})", name="ck_capability_public_availability"
        ),
        CheckConstraint(f"claim_state IN ({_enum_sql(ClaimTreatment)})", name="ck_capability_claim_state"),
        CheckConstraint(
            "current_promotion_sequence >= 0",
            name="ck_capability_current_sequence_nonnegative",
        ),
        Index("ix_capability_readiness_tenant_company", "tenant_id", "company_id"),
        Index("ix_capability_readiness_company_id", "company_id"),
        Index("ix_capability_readiness_capability_id", "capability_id"),
        Index(
            "uq_capability_readiness_tenant_global",
            "tenant_id",
            "capability_id",
            unique=True,
            postgresql_where=text("company_id IS NULL"),
            sqlite_where=text("company_id IS NULL"),
        ),
        Index(
            "uq_capability_readiness_company",
            "tenant_id",
            "company_id",
            "capability_id",
            unique=True,
            postgresql_where=text("company_id IS NOT NULL"),
            sqlite_where=text("company_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=True)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    domain: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    scope_disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_details: Mapped[dict[str, object]] = mapped_column(READINESS_JSON, nullable=False, default=dict)
    required_gate_ids: Mapped[list[str]] = mapped_column(READINESS_JSON, nullable=False, default=list)
    internal_maturity_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=InternalMaturity.MISSING.value, server_default=text("'Missing'")
    )
    release_gate_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=GateResult.BLOCKED.value, server_default=text("'Blocked'")
    )
    public_availability_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PublicAvailability.UNAVAILABLE.value, server_default=text("'Unavailable'")
    )
    claim_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ClaimTreatment.HIDDEN.value, server_default=text("'Hidden'")
    )
    gate_results: Mapped[dict[str, object]] = mapped_column(READINESS_JSON, nullable=False, default=dict)
    permitted_claim_ids: Mapped[list[str]] = mapped_column(READINESS_JSON, nullable=False, default=list)
    owners: Mapped[dict[str, str]] = mapped_column(READINESS_JSON, nullable=False, default=dict)
    approver_ids: Mapped[list[str]] = mapped_column(READINESS_JSON, nullable=False, default=list)
    traceability: Mapped[dict[str, list[str]]] = mapped_column(READINESS_JSON, nullable=False, default=dict)
    limitations: Mapped[list[str]] = mapped_column(READINESS_JSON, nullable=False, default=list)
    feature_flag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    review_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    current_promotion_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True, onupdate=func.now())


class CapabilityEvidenceRecord(BaseModel):
    __tablename__ = "capability_evidence_records"
    __table_args__ = (
        CheckConstraint(
            f"environment IN ({_enum_sql(EvidenceEnvironment)})", name="ck_capability_evidence_environment"
        ),
        CheckConstraint(
            "length(sha256_checksum) = 64 AND lower(sha256_checksum) = sha256_checksum",
            name="ck_capability_evidence_checksum",
        ),
        CheckConstraint(
            "observed_at < expires_at AND "
            "(reviewed_at IS NULL OR (observed_at <= reviewed_at AND reviewed_at < expires_at))",
            name="ck_capability_evidence_times",
        ),
        CheckConstraint(f"trust_state IN ({_enum_sql(EvidenceTrustState)})", name="ck_capability_evidence_trust_state"),
        CheckConstraint(
            "length(source_commit_sha) >= 7 AND length(source_commit_sha) <= 64",
            name="ck_capability_evidence_commit_length",
        ),
        Index("ix_capability_evidence_scope", "tenant_id", "company_id", "capability_id"),
        Index("ix_capability_evidence_company_id", "company_id"),
        Index("ix_capability_evidence_expiry", "expires_at"),
        Index("uq_capability_evidence_version", "readiness_record_id", "evidence_version", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    readiness_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capability_readiness_records.id", ondelete="RESTRICT"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=True)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    evidence_version: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_account_class: Mapped[str] = mapped_column(String(100), nullable=False)
    product_version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    trust_state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=EvidenceTrustState.UNVERIFIED.value,
        server_default=text("'unverified'"),
    )
    submitted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    supports_gate_ids: Mapped[list[str]] = mapped_column(READINESS_JSON, nullable=False, default=list)
    supports_claim_ids: Mapped[list[str]] = mapped_column(READINESS_JSON, nullable=False, default=list)
    evidence_metadata: Mapped[dict[str, object]] = mapped_column(READINESS_JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class CapabilityPromotionEvent(BaseModel):
    __tablename__ = "capability_promotion_events"
    __table_args__ = (
        Index("ix_capability_promotion_scope", "tenant_id", "company_id", "capability_id"),
        Index("uq_capability_promotion_sequence", "readiness_record_id", "sequence", unique=True),
        CheckConstraint(
            "event_type IN ('registered', 'promoted', 'demoted', 'attested', 'review_renewed', 'owners_rotated')",
            name="ck_capability_promotion_event_type",
        ),
        CheckConstraint("sequence >= 0", name="ck_capability_promotion_sequence_nonnegative"),
        CheckConstraint(
            "length(event_hash) = 64 AND lower(event_hash) = event_hash",
            name="ck_capability_promotion_event_hash",
        ),
        CheckConstraint(
            "previous_event_hash IS NULL OR "
            "(length(previous_event_hash) = 64 AND lower(previous_event_hash) = previous_event_hash)",
            name="ck_capability_promotion_previous_hash",
        ),
        Index("ix_capability_promotion_company_id", "company_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    readiness_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capability_readiness_records.id", ondelete="RESTRICT"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=True)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_internal_maturity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_internal_maturity: Mapped[str] = mapped_column(String(32), nullable=False)
    from_release_gate: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_release_gate: Mapped[str] = mapped_column(String(32), nullable=False)
    from_public_availability: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_public_availability: Mapped[str] = mapped_column(String(32), nullable=False)
    from_claim_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_claim_state: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_disposition_snapshot: Mapped[str] = mapped_column(String(32), nullable=False)
    gate_results_snapshot: Mapped[dict[str, object]] = mapped_column(READINESS_JSON, nullable=False, default=dict)
    evidence_snapshot: Mapped[list[dict[str, object]]] = mapped_column(READINESS_JSON, nullable=False, default=list)
    ownership_snapshot: Mapped[dict[str, object]] = mapped_column(READINESS_JSON, nullable=False, default=dict)
    traceability_snapshot: Mapped[dict[str, object]] = mapped_column(READINESS_JSON, nullable=False, default=dict)
    permitted_claim_ids: Mapped[list[str]] = mapped_column(READINESS_JSON, nullable=False, default=list)
    limitations: Mapped[list[str]] = mapped_column(READINESS_JSON, nullable=False, default=list)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
