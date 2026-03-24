"""Lead pipeline and email sequence ORM models for sales agent."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class LeadPipeline(BaseModel):
    __tablename__ = "lead_pipeline"
    __table_args__ = (
        Index("idx_lead_pipeline_tenant", "tenant_id", "stage"),
        Index("idx_lead_pipeline_email", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    # Lead info
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="website")

    # Pipeline state
    stage: Mapped[str] = mapped_column(String(50), nullable=False, default="new")
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_factors: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Assignment
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    assigned_human: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Qualification (BANT)
    budget: Mapped[str | None] = mapped_column(String(100), nullable=True)
    authority: Mapped[str | None] = mapped_column(String(100), nullable=True)
    need: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeline: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Tracking
    last_contacted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    next_followup_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    followup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    demo_scheduled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    trial_started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    deal_value_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # UTM tracking
    utm_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(100), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(100), nullable=True)
    page_visits: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    emails = relationship("EmailSequence", back_populates="lead")


class EmailSequence(BaseModel):
    __tablename__ = "email_sequences"
    __table_args__ = (
        Index("idx_email_sequences_lead", "lead_id", "sequence_name", "step_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lead_pipeline.id"), nullable=False)
    sequence_name: Mapped[str] = mapped_column(String(100), nullable=False, default="initial_outreach")
    step_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    email_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    lead = relationship("LeadPipeline", back_populates="emails")
