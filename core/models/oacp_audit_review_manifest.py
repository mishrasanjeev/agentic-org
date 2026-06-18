"""Durable OACP audit review manifest model.

The table stores only redacted, audit-safe review manifest metadata for
AgenticOrg internal review and retention boundaries. It does not write export
files and is not transaction authority.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, TIMESTAMP, Boolean, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

OACP_AUDIT_REVIEW_MANIFEST_JSON = JSONB().with_variant(JSON(), "sqlite")


class OacpAuditReviewManifestRecordRow(BaseModel):
    __tablename__ = "oacp_audit_review_manifest_records"
    __table_args__ = (
        Index("ix_oacp_audit_review_manifest_tenant_id", "tenant_id"),
        Index("ix_oacp_audit_review_manifest_merchant_id", "merchant_id"),
        Index("ix_oacp_audit_review_manifest_seller_agent_id", "seller_agent_id"),
        Index("ix_oacp_audit_review_manifest_buyer_agent_id", "buyer_agent_id"),
        Index("ix_oacp_audit_review_manifest_bundle_id", "bundle_id"),
        Index("ix_oacp_audit_review_manifest_retention_class", "retention_class"),
        Index("ix_oacp_audit_review_manifest_retain_until", "retain_until"),
        Index("ix_oacp_audit_review_manifest_generated_at", "generated_at"),
    )

    manifest_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    bundle_id: Mapped[str] = mapped_column(String(180), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    bundle_generated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    buyer_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    scope_summary: Mapped[dict[str, object]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=dict,
    )
    retention_class: Mapped[str] = mapped_column(String(64), nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    retain_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    retention_clock_source: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_family_counts: Mapped[dict[str, object]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=dict,
    )
    cache_record_references: Mapped[list[str]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=list,
    )
    maintenance_plan_references: Mapped[list[str]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=list,
    )
    review_packet_references: Mapped[list[str]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=list,
    )
    decision_record_references: Mapped[list[str]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=list,
    )
    redacted_reason_codes: Mapped[dict[str, object]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=dict,
    )
    redacted_source_refs: Mapped[list[str]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=list,
    )
    redacted_evidence_refs: Mapped[list[str]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=list,
    )
    freshness_ttl_summary: Mapped[dict[str, object]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=dict,
    )
    revocation_snapshot_summary: Mapped[dict[str, object]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=dict,
    )
    risk_tier_summary: Mapped[dict[str, object]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=dict,
    )
    unsupported_capability_summary: Mapped[dict[str, object]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=dict,
    )
    blocked_capability_summary: Mapped[dict[str, object]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=dict,
    )
    next_step_labels: Mapped[list[str]] = mapped_column(
        OACP_AUDIT_REVIEW_MANIFEST_JSON,
        nullable=False,
        default=list,
    )
    allowed_to_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    no_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    review_manifest_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    retention_boundary_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    audit_export_bundle_review_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    export_file_written: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    export_writer_added: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    generated_artifact_written: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    non_authoritative_for_transaction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    no_checkout_payment_enablement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    no_live_provider_enablement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    no_public_discovery_enablement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
