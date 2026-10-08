# SPDX-License-Identifier: Apache-2.0
"""A banking conversation in progress: its dialogue state per channel, company, agent and user."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ConversationSession(BaseModel):
    """The dialogue of one session key (``core/conversation/runtime.py``).

    Row-level security: tenant-scoped (``v6z60_conversation_sessions``).
    """

    __tablename__ = "conversation_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'idle', 'escalated')", name="ck_conversation_sessions_status"),
        Index("ux_conversation_sessions_tenant_key", "tenant_id", "session_key", unique=True),
        Index("ix_conversation_sessions_tenant_updated", "tenant_id", "updated_at"),
        # Leads with the foreign key.
        Index("ix_conversation_sessions_agent_id", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_key: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The hand-off record and who holds the session (``v6z61_conversation_supervision``).
    escalation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    taken_over_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    taken_over_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
