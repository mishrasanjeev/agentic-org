# SPDX-License-Identifier: Apache-2.0
"""Payment execution records: every Plural call an executor makes, and RBI AFA evidence for UPI AutoPay.

Row-level security: both tables are tenant-scoped with ENABLE + FORCE RLS
(``v6z90_mandates_rails``) and append-only (trigger function
``commerce_payment_record_reject_mutation``): a call is recorded as a
``requested`` row before it is sent and an outcome row after, never updated.
``idempotency_key`` is the merchant reference sent to the provider, derived
from the session (or order) and the consumed decision, so a second attempt
with the same decision finds the first one instead of calling again. No
credential, token or provider secret is stored; ``details`` holds status codes
and digests only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, BigInteger, CheckConstraint, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

PAYMENT_OPERATIONS: tuple[str, ...] = ("create_order", "create_payment", "capture", "refund")
PAYMENT_PHASES: tuple[str, ...] = ("requested", "succeeded", "failed", "unknown")
AFA_PHASES: tuple[str, ...] = ("requested", "authorized", "failed")


class CommercePaymentOperationRow(BaseModel):
    __tablename__ = "commerce_payment_operations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", "operation", "phase", name="uq_commerce_payment_operations"),
        Index("ix_commerce_payment_operations_session", "tenant_id", "checkout_session_id"),
        CheckConstraint(
            "operation IN ('create_order', 'create_payment', 'capture', 'refund')",
            name="ck_commerce_payment_operations_operation",
        ),
        CheckConstraint(
            "phase IN ('requested', 'succeeded', 'failed', 'unknown')", name="ck_commerce_payment_operations_phase"
        ),
        CheckConstraint(
            "amount_minor_units IS NULL OR amount_minor_units >= 0", name="ck_commerce_payment_operations_amount"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    checkout_session_id: Mapped[str] = mapped_column(String(80), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decision_id: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    operation: Mapped[str] = mapped_column(String(24), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount_minor_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    mandate_hashes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class CommerceAfaEvidenceRow(BaseModel):
    __tablename__ = "commerce_afa_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "mandate_reference", "terms_sha256", "phase", name="uq_commerce_afa_evidence_phase"
        ),
        Index("ix_commerce_afa_evidence_session", "tenant_id", "checkout_session_id"),
        CheckConstraint("phase IN ('requested', 'authorized', 'failed')", name="ck_commerce_afa_evidence_phase"),
        CheckConstraint("terms_sha256 ~ '^[0-9a-f]{64}$'", name="ck_commerce_afa_evidence_terms"),
        CheckConstraint(
            "(phase = 'authorized') = (evidence_ref IS NOT NULL)", name="ck_commerce_afa_evidence_ref"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    checkout_session_id: Mapped[str] = mapped_column(String(80), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(120), nullable=False)
    mandate_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    terms_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_event_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
