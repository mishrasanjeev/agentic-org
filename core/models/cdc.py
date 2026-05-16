"""CDC webhook ingestion ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class CDCEvent(BaseModel):
    """Durable, tenant-scoped CDC webhook event record."""

    __tablename__ = "cdc_events"
    __table_args__ = (
        Index(
            "uq_cdc_events_tenant_connector_fingerprint",
            "tenant_id",
            "connector",
            "fingerprint",
            unique=True,
        ),
        Index("ix_cdc_events_tenant_id", "tenant_id"),
        Index("ix_cdc_events_connector", "connector"),
        Index("ix_cdc_events_event_hash", "event_hash"),
        Index("ix_cdc_events_tenant_connector_status", "tenant_id", "connector", "processing_status"),
        Index("ix_cdc_events_tenant_event_received", "tenant_id", "event_type", "received_at"),
        Index(
            "uq_cdc_events_tenant_connector_provider_event",
            "tenant_id",
            "connector",
            "provider_event_id",
            unique=True,
            postgresql_where=text("provider_event_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    connector: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_body_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature_verification_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="valid", server_default="valid"
    )
    processing_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="received", server_default="received"
    )
    processing_outcome: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    replay_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_replayed", server_default="not_replayed"
    )
    processed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("FALSE")
    )
    replay_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_replayed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_replayed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class CDCEventDeadLetter(BaseModel):
    """Append-only record for CDC events that failed downstream processing."""

    __tablename__ = "cdc_event_dead_letters"
    __table_args__ = (
        Index("ix_cdc_event_dead_letters_tenant_created", "tenant_id", "created_at"),
        Index("ix_cdc_event_dead_letters_event", "cdc_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cdc_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cdc_events.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    connector: Mapped[str] = mapped_column(String(100), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_stage: Mapped[str] = mapped_column(String(100), nullable=False)
    error_code: Mapped[str] = mapped_column(String(100), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    error_details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    replayable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("TRUE")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
