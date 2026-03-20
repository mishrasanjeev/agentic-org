"""Workflow ORM models."""
from __future__ import annotations
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import (
    TIMESTAMP, Boolean, ForeignKey, Index, Integer, Numeric,
    SmallInteger, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.models.base import BaseModel

class WorkflowDefinition(BaseModel):
    __tablename__ = "workflow_definitions"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domain: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trigger_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    trigger_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    runs = relationship("WorkflowRun", back_populates="workflow_def")

class WorkflowRun(BaseModel):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("idx_wf_runs_tenant_status", "tenant_id", "status"),
        Index("idx_wf_runs_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    workflow_def_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_definitions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    trigger_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    steps_total: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    steps_completed: Mapped[int] = mapped_column(SmallInteger, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    timeout_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    workflow_def = relationship("WorkflowDefinition", back_populates="runs")
    steps = relationship("StepExecution", back_populates="workflow_run")

class StepExecution(BaseModel):
    __tablename__ = "step_executions"
    __table_args__ = (Index("idx_step_exec_run", "workflow_run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step_id: Mapped[str] = mapped_column(String(100), nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    input: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    output: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3), nullable=True)
    reasoning_trace: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    retry_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    workflow_run = relationship("WorkflowRun", back_populates="steps", foreign_keys=[workflow_run_id])
