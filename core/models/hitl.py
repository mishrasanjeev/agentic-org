"""HITL Queue ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class HITLQueue(BaseModel):
    __tablename__ = "hitl_queue"
    __table_args__ = (
        Index(
            "ix_hitl_queue_tenant_status_created",
            "tenant_id",
            "status",
            text("created_at DESC"),
        ),
        # Migration v6z23: a checkpoint thread can only be stored on its own
        # tenant's row (core/langgraph/thread_ids.py).
        CheckConstraint(
            "checkpoint_thread_id IS NULL OR "
            "starts_with(checkpoint_thread_id, 'tenant:' || tenant_id::text || ':')",
            name="ck_hitl_queue_checkpoint_thread_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    assignee_role: Mapped[str] = mapped_column(String(100), nullable=False)
    decision_options: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Bug sheet 2026-09-14 row 30 (migration v6z21): who triggered the item.
    # Visibility is derived from the agent's owner (core/ownership.py).
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL", name="fk_hitl_queue_requested_by_user_id"),
        nullable=True,
    )
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Migration v6z23: the server-generated, tenant-prefixed LangGraph thread
    # of the paused standalone run. Never returned by the API and never taken
    # from a request; the only way to reach a checkpoint.
    checkpoint_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decision_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
