# SPDX-License-Identifier: Apache-2.0
"""Case push endpoints, the push outbox and provider webhook receipts (see ``core.cases.push``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

PUSH_JSON = JSONB().with_variant(JSON(), "sqlite")


class CasePushEndpoint(BaseModel):
    """Where a tenant's cases are pushed, and the HMAC keys that sign each delivery.

    ``signing_keys_encrypted`` holds ``{"_encrypted": <encrypt_for_tenant output>}`` of a JSON list
    of ``{"key_id", "secret", "created_at"}``, newest first; plaintext secrets are never stored.
    """

    __tablename__ = "case_push_endpoints"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_case_push_endpoints_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    signing_keys_encrypted: Mapped[dict[str, Any]] = mapped_column(PUSH_JSON, nullable=False, default=dict)
    active_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class CasePushOutbox(BaseModel):
    """One ``case_push`` event, written in the same transaction as the case change that caused it."""

    __tablename__ = "case_push_outbox"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_case_push_outbox_tenant_event"),
        CheckConstraint("status IN ('pending', 'delivered', 'dead_lettered')", name="ck_case_push_outbox_status"),
        CheckConstraint("attempts >= 0", name="ck_case_push_outbox_attempts"),
        Index("ix_case_push_outbox_tenant_status_due", "tenant_id", "status", "next_attempt_at"),
        Index("ix_case_push_outbox_case", "case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_cases.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(PUSH_JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    replay_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class ProviderWebhookReceipt(BaseModel):
    """Every inbound provider webhook: whether it verified and what was done. The body is never stored."""

    __tablename__ = "provider_webhook_receipts"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('accepted', 'duplicate', 'unverified')", name="ck_provider_webhook_receipts_outcome"
        ),
        Index(
            "uq_provider_webhook_receipts_accepted_event",
            "tenant_id",
            "provider",
            "event_id",
            unique=True,
            postgresql_where=text("outcome = 'accepted'"),
        ),
        Index("ix_provider_webhook_receipts_tenant_received", "tenant_id", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    requeried_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
