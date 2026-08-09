"""Audit log ORM model — append-only with HMAC signature."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class AuditLog(BaseModel):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_tenant_created", "tenant_id", text("created_at DESC")),
        Index(
            "ix_audit_log_tenant_agent_created",
            "tenant_id",
            "agent_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_audit_log_tenant_company_created",
            "tenant_id",
            "company_id",
            text("created_at DESC"),
        ),
        Index("ix_audit_log_tenant_actor", "tenant_id", "actor_id"),
        Index(
            "ix_audit_log_tool_outcome_created",
            "tenant_id",
            "outcome",
            text("created_at DESC"),
            postgresql_where=text("resource_type = 'tool_call'"),
        ),
        Index(
            "ix_audit_log_tool_connector_created",
            "tenant_id",
            text("(details->>'connector')"),
            text("created_at DESC"),
            postgresql_where=text("resource_type = 'tool_call'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    signature: Mapped[str | None] = mapped_column(String(512), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
