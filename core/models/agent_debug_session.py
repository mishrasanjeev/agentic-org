# SPDX-License-Identifier: Apache-2.0
"""A run paused at a breakpoint, stepped from the debugging console (``core/langgraph/debugger.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class AgentDebugSession(BaseModel):
    """One debug session per paused thread: where it stopped, what it pauses before, and how to re-enter it.

    ``spec`` holds the run's graph parameters for the next step and is never
    returned by the API. Row-level security: tenant-scoped
    (``v6z59_agent_debug_sessions``).
    """

    __tablename__ = "agent_debug_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('paused', 'running', 'completed', 'failed')", name="ck_agent_debug_sessions_status"
        ),
        Index("ux_agent_debug_sessions_tenant_thread", "tenant_id", "thread_id", unique=True),
        Index("ix_agent_debug_sessions_tenant_created", "tenant_id", "created_at"),
        # Leads with the foreign key.
        Index("ix_agent_debug_sessions_agent_id", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="paused")
    paused_before: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    breakpoints: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    steps_taken: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
