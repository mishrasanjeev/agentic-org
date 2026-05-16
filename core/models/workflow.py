"""Workflow ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class WorkflowDefinition(BaseModel):
    __tablename__ = "workflow_definitions"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str | None] = mapped_column(String(50), nullable=True)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trigger_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    trigger_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    runs = relationship("WorkflowRun", back_populates="workflow_def")


class WorkflowRun(BaseModel):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("idx_wf_runs_tenant_status", "tenant_id", "status"),
        Index("idx_wf_runs_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_def_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_definitions.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    trigger_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    steps_total: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    steps_completed: Mapped[int] = mapped_column(SmallInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    workflow_def = relationship("WorkflowDefinition", back_populates="runs")
    steps = relationship("StepExecution", back_populates="workflow_run")


class WorkflowRunState(BaseModel):
    __tablename__ = "workflow_run_states"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_workflow_run_states_run_id"),
        Index("ix_workflow_run_states_tenant_status", "tenant_id", "status"),
        Index("ix_workflow_run_states_workflow_run_id", "workflow_run_id"),
        Index("ix_workflow_run_states_updated_at", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[str] = mapped_column(String(100), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    waiting_step_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
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


class WorkflowStateTransition(BaseModel):
    __tablename__ = "workflow_state_transitions"
    __table_args__ = (
        Index("ix_workflow_state_transitions_run_created", "run_id", "created_at"),
        Index("ix_workflow_state_transitions_step", "run_id", "step_id"),
        Index(
            "uq_workflow_state_transitions_idempotency",
            "run_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("workflow_run_states.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    step_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    previous_state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="workflow_engine")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transition_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class StepExecution(BaseModel):
    __tablename__ = "step_executions"
    __table_args__ = (Index("idx_step_exec_run", "workflow_run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(100), nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    reasoning_trace: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    retry_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    workflow_run = relationship(
        "WorkflowRun", back_populates="steps", foreign_keys=[workflow_run_id]
    )
