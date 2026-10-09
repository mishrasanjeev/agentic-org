# SPDX-License-Identifier: Apache-2.0
"""The agent registry: an agent's card fields and its governance lifecycle (``core/agent_registry``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class AgentRegistryEntry(BaseModel):
    """What the registry knows about an agent beyond its runtime configuration.

    The card fields an administrator writes (purpose, risk tier, use case,
    channels) and the governance state the agent is in: ``draft``,
    ``review``, ``approved``, ``published``, ``deprecated`` or ``retired``.
    One row per agent. The runtime ``status`` of the agent (shadow, active,
    paused) is a different thing and stays on the agent.

    Row-level security: tenant-scoped (``v6z48_agent_registry``).
    """

    __tablename__ = "agent_registry"
    __table_args__ = (
        CheckConstraint(
            "state IN ('draft','review','approved','published','deprecated','retired')",
            name="ck_agent_registry_state",
        ),
        CheckConstraint(
            "risk_tier IS NULL OR risk_tier IN ('low','medium','high','critical')",
            name="ck_agent_registry_risk_tier",
        ),
        # Leads with the foreign key: one entry per agent.
        Index("ux_agent_registry_agent", "agent_id", unique=True),
        Index("ix_agent_registry_tenant_state", "tenant_id", "state", "risk_tier"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    use_case: Mapped[str | None] = mapped_column(String(120), nullable=True)
    channels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    state_changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    state_changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Who moved the agent into review: the approval may not come from the same person.
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class AgentRegistryEvent(BaseModel):
    """One lifecycle transition of an agent in the registry, who made it and why.

    Row-level security: tenant-scoped (``v6z48_agent_registry``).
    """

    __tablename__ = "agent_registry_events"
    __table_args__ = (
        CheckConstraint(
            "to_state IN ('draft','review','approved','published','deprecated','retired')",
            name="ck_agent_registry_events_to",
        ),
        # Leads with the foreign key: the transitions of one agent, in order.
        Index("ix_agent_registry_events_agent_created", "agent_id", "created_at"),
        Index("ix_agent_registry_events_tenant_created", "tenant_id", "created_at"),
        CheckConstraint(
            "from_state IN ('draft','review','approved','published','deprecated','retired')",
            name="ck_agent_registry_events_from",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
