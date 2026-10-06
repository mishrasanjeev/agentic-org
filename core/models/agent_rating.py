# SPDX-License-Identifier: Apache-2.0
"""A user's rating of an agent (``core/agent_registry/reliability.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class AgentRating(BaseModel):
    """One user's current rating of an agent, 1 to 5 with a short comment; a new rating replaces the old.

    Row-level security: tenant-scoped (``v6z49_agent_ratings``).
    """

    __tablename__ = "agent_ratings"
    __table_args__ = (
        CheckConstraint("score >= 1 AND score <= 5", name="ck_agent_ratings_score"),
        # Leads with the foreign key: one rating per user and agent.
        Index("ux_agent_ratings_agent_user", "agent_id", "user_id", unique=True),
        Index("ix_agent_ratings_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
