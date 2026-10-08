# SPDX-License-Identifier: Apache-2.0
"""A content draft and its approval state (``core/content/drafts.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, CheckConstraint, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ContentDraft(BaseModel):
    """What a content service produced, the sources it used, and whether a second person approved it.

    Row-level security: tenant-scoped (``v6z62_content_drafts``).
    """

    __tablename__ = "content_drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected')", name="ck_content_drafts_status"
        ),
        Index("ix_content_drafts_tenant_status_created", "tenant_id", "status", "created_at"),
        Index("ix_content_drafts_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="memo")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    sources: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    guardrails: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
