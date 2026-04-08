"""ConnectorConfig ORM model — per-tenant connector credentials and config."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ConnectorConfig(BaseModel):
    __tablename__ = "connector_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "connector_name", name="uq_connector_config_tenant"),
        Index("ix_connector_configs_tenant", "tenant_id"),
        Index("ix_connector_configs_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    connector_name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="api_key"
    )
    credentials_encrypted: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="configured"
    )
    last_health_check: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    health_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default="unknown"
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True
    )
