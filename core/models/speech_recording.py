# SPDX-License-Identifier: Apache-2.0
"""A kept recording with its segments, speakers and encrypted transcript (``core/speech/store.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Float, Index, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class SpeechRecording(BaseModel):
    """The audio, what the diariser found, and the transcript under the tenant key. Tenant-scoped row-level security."""

    __tablename__ = "speech_recordings"
    __table_args__ = (
        Index("ix_speech_recordings_tenant_created", "tenant_id", "created_at"),
        Index("ix_speech_recordings_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="audio/wav")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channels: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    channel_roles: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    segments: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    speakers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    transcript_encrypted: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    summary_encrypted: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    analytics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    redactions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    redacted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
