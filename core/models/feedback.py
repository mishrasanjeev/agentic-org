"""Durable, tenant-scoped feedback captured from people and HITL gates."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import TIMESTAMP, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class AgentFeedback(BaseModel):
    __tablename__ = "agent_feedback"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_event_id",
            name="uq_agent_feedback_tenant_source_event",
        ),
        Index(
            "ix_agent_feedback_agent_tenant_created",
            "agent_id",
            "tenant_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(String(200), nullable=False)
    feedback_type: Mapped[str] = mapped_column(String(30), nullable=False)
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    corrected_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    source_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(100), nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence_before: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    confidence_after: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    learning_weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("1.00")
    )
    applied_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
