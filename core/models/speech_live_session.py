# SPDX-License-Identifier: Apache-2.0
"""A live call session for agent assist: turns encrypted, flags raised, the closing report (core/speech/assist.py)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class SpeechLiveSession(BaseModel):
    """One call as it happens. Row-level security: tenant-scoped, forced."""

    __tablename__ = "speech_live_sessions"
    __table_args__ = (
        Index("ix_speech_live_sessions_tenant_started", "tenant_id", "started_at"),
        Index("ix_speech_live_sessions_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    call_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    call_type: Mapped[str] = mapped_column(String(32), nullable=False, default="service")
    required: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    turns_encrypted: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
