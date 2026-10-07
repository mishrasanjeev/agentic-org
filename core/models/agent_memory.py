# SPDX-License-Identifier: Apache-2.0
"""A long-term memory entry about a subject (``core/memory/long_term.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class AgentMemory(BaseModel):
    """What an agent, or an administrator, chose to remember about a subject, with its expiry.

    Row-level security: tenant-scoped (``v6z57_agent_memories``).
    """

    __tablename__ = "agent_memories"
    __table_args__ = (
        CheckConstraint("kind IN ('fact', 'preference', 'summary', 'event')", name="ck_agent_memories_kind"),
        CheckConstraint("importance >= 1 AND importance <= 5", name="ck_agent_memories_importance"),
        CheckConstraint("source IN ('api', 'run')", name="ck_agent_memories_source"),
        Index("ix_agent_memories_tenant_subject", "tenant_id", "subject"),
        Index("ix_agent_memories_tenant_expires", "tenant_id", "expires_at"),
        # Leads with the foreign key.
        Index("ix_agent_memories_agent_id", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="fact")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="api")
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_recalled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    recall_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
