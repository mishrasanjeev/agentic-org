"""Durable OACP retention disposition decision model.

The table stores only redacted, audit-safe retention disposition decision
metadata for AgenticOrg internal review. It does not execute retention, delete
records, write export files, schedule work, or provide transaction authority.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, TIMESTAMP, Boolean, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

OACP_RETENTION_DISPOSITION_DECISION_JSON = JSONB().with_variant(JSON(), "sqlite")


class OacpRetentionDispositionDecisionRecordRow(BaseModel):
    __tablename__ = "oacp_retention_disposition_decision_records"
    __table_args__ = (
        Index("ix_oacp_retention_disposition_decision_tenant_id", "tenant_id"),
        Index("ix_oacp_retention_disposition_decision_merchant_id", "merchant_id"),
        Index("ix_oacp_retention_disposition_decision_seller_agent_id", "seller_agent_id"),
        Index("ix_oacp_retention_disposition_decision_buyer_agent_id", "buyer_agent_id"),
        Index("ix_oacp_retention_disposition_decision_kind", "decision_kind"),
        Index("ix_oacp_retention_disposition_decision_summary_id", "source_summary_id"),
        Index("ix_oacp_retention_disposition_decision_dry_run_id", "source_dry_run_id"),
        Index("ix_oacp_retention_disposition_decision_packet_id", "source_operator_packet_id"),
        Index("ix_oacp_retention_disposition_decision_retention_class", "retention_class"),
        Index("ix_oacp_retention_disposition_decision_retain_until", "retain_until"),
        Index("ix_oacp_retention_disposition_decision_decided_at", "decided_at"),
    )

    disposition_decision_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    source_summary_id: Mapped[str] = mapped_column(String(180), nullable=False)
    source_dry_run_id: Mapped[str] = mapped_column(String(180), nullable=False)
    source_operator_packet_id: Mapped[str] = mapped_column(String(180), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    buyer_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    decision_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    retention_class: Mapped[str] = mapped_column(String(64), nullable=False)
    retain_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    manifest_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retention_due_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    legal_hold_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    artifact_family_counts: Mapped[dict[str, object]] = mapped_column(
        OACP_RETENTION_DISPOSITION_DECISION_JSON,
        nullable=False,
        default=dict,
    )
    risk_tier_counts: Mapped[dict[str, object]] = mapped_column(
        OACP_RETENTION_DISPOSITION_DECISION_JSON,
        nullable=False,
        default=dict,
    )
    blocked_capability_summary: Mapped[dict[str, object]] = mapped_column(
        OACP_RETENTION_DISPOSITION_DECISION_JSON,
        nullable=False,
        default=dict,
    )
    unsupported_capability_summary: Mapped[dict[str, object]] = mapped_column(
        OACP_RETENTION_DISPOSITION_DECISION_JSON,
        nullable=False,
        default=dict,
    )
    redacted_evidence_ref_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    redacted_reason_codes: Mapped[dict[str, object]] = mapped_column(
        OACP_RETENTION_DISPOSITION_DECISION_JSON,
        nullable=False,
        default=dict,
    )
    reviewer_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    scope_summary: Mapped[dict[str, object]] = mapped_column(
        OACP_RETENTION_DISPOSITION_DECISION_JSON,
        nullable=False,
        default=dict,
    )
    next_step_labels: Mapped[list[str]] = mapped_column(
        OACP_RETENTION_DISPOSITION_DECISION_JSON,
        nullable=False,
        default=list,
    )
    future_retention_action_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    records_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retention_executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allowed_to_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    no_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    retention_disposition_decision_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    durable_disposition_decision_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    export_file_written: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    export_writer_added: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scheduler_added: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cli_added: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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
