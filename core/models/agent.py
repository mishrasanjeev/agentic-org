"""Agent and scaling ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class Agent(BaseModel):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_type", "employee_name", "version"),
        Index("idx_agents_tenant_domain", "tenant_id", "domain"),
        Index("idx_agents_routing", "tenant_id", "agent_type", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(100), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    prompt_variables: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    llm_model: Mapped[str] = mapped_column(
        String(100), nullable=False, default="claude-3-5-sonnet-20241022"
    )
    llm_fallback: Mapped[str | None] = mapped_column(String(100), nullable=True)
    llm_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence_floor: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0.880")
    )
    hitl_condition: Mapped[str] = mapped_column(Text, nullable=False)
    max_retries: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    retry_backoff: Mapped[str] = mapped_column(String(20), nullable=False, default="exponential")
    authorized_tools: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    output_schema: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="shadow")
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    parent_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    shadow_comparison_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    shadow_min_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    shadow_accuracy_floor: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0.950")
    )
    shadow_sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shadow_accuracy_current: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    cost_controls: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scaling: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    tags: Mapped[list] = mapped_column(ARRAY(String(50)), nullable=False, default=list)
    ttl_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Virtual employee persona fields
    employee_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    designation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    specialization: Mapped[str | None] = mapped_column(String(500), nullable=True)
    routing_filter: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    system_prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reporting_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant", back_populates="agents")
    parent_agent = relationship("Agent", remote_side="Agent.id", foreign_keys=[parent_agent_id])
    versions = relationship("AgentVersion", back_populates="agent")
    lifecycle_events = relationship("AgentLifecycleEvent", back_populates="agent")


class AgentVersion(BaseModel):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    authorized_tools: Mapped[list] = mapped_column(JSONB, nullable=False)
    hitl_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    llm_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence_floor: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    is_verified_good: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    deployed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    agent = relationship("Agent", back_populates="versions")


class AgentLifecycleEvent(BaseModel):
    __tablename__ = "agent_lifecycle_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    from_status: Mapped[str] = mapped_column(String(30), nullable=False)
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(30), nullable=False)
    triggered_by_user: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    shadow_accuracy: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    shadow_samples: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    agent = relationship("Agent", back_populates="lifecycle_events")


class AgentTeam(BaseModel):
    __tablename__ = "agent_teams"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(50), nullable=True)
    routing_rules: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    members = relationship("AgentTeamMember", back_populates="team")


class AgentTeamMember(BaseModel):
    __tablename__ = "agent_team_members"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_teams.id"), primary_key=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=Decimal("1.0"))
    added_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    team = relationship("AgentTeam", back_populates="members")
    agent = relationship("Agent")


class AgentCostLedger(BaseModel):
    __tablename__ = "agent_cost_ledger"
    __table_args__ = (UniqueConstraint("tenant_id", "agent_id", "period_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    period_date: Mapped[datetime] = mapped_column(nullable=False)
    token_count: Mapped[int] = mapped_column(nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=Decimal("0"))
    task_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_per_task: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    budget_pct_used: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class ShadowComparison(BaseModel):
    __tablename__ = "shadow_comparisons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    shadow_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    reference_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    outputs_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    match_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    shadow_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    shadow_hitl_would_trigger: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reference_hitl_triggered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    shadow_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
