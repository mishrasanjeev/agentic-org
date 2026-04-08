"""AgentTaskResult ORM model — execution history for all agent tasks."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Float,
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


class AgentTaskResult(BaseModel):
    __tablename__ = "agent_task_results"
    __table_args__ = (
        Index("ix_agent_results_tenant", "tenant_id"),
        Index("ix_agent_results_agent", "agent_id"),
        Index("ix_agent_results_domain", "tenant_id", "domain"),
        Index("ix_agent_results_created", "created_at"),
        Index("ix_agent_results_type", "agent_type", "task_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    agent_type: Mapped[str] = mapped_column(String(100), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    task_input: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    task_output: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_calls: Mapped[list] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb")
    )
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="completed"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    hitl_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    hitl_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
