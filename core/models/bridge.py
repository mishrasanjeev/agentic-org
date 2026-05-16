"""Bridge Registry ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel, TenantMixin, TimestampMixin


class BridgeRegistration(BaseModel, TenantMixin, TimestampMixin):
    __tablename__ = "bridge_registry"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bridge_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    bridge_type: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict] = mapped_column("metadata_", JSONB, server_default="{}")


class BridgeSession(BaseModel, TenantMixin, TimestampMixin):
    __tablename__ = "bridge_sessions"
    __table_args__ = (
        Index("ix_bridge_sessions_tenant_status", "tenant_id", "status"),
        Index("ix_bridge_sessions_tenant_type", "tenant_id", "connector_type"),
        Index("ix_bridge_sessions_last_heartbeat", "last_heartbeat"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bridge_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("bridge_registry.bridge_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False, default="tally")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="disconnected")
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tally_healthy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    connection_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    process_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reconnect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    session_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class BridgeRequest(BaseModel, TenantMixin, TimestampMixin):
    __tablename__ = "bridge_requests"
    __table_args__ = (
        Index("ix_bridge_requests_request_id", "request_id", unique=True),
        Index("ix_bridge_requests_tenant_status", "tenant_id", "status"),
        Index("ix_bridge_requests_bridge_status", "bridge_id", "status"),
        Index("ix_bridge_requests_expires_at", "expires_at"),
        Index(
            "uq_bridge_requests_idempotency",
            "tenant_id",
            "bridge_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    bridge_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("bridge_registry.bridge_id", ondelete="CASCADE"),
        nullable=False,
    )
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False, default="tally")
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
