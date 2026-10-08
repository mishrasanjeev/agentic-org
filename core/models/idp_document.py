# SPDX-License-Identifier: Apache-2.0
"""A processed document kept for review: the file, the pipeline's result, the corrections and the decision."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, CheckConstraint, Index, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class IdpDocument(BaseModel):
    """What ``POST /idp/analyse`` kept (``core/idp/store.py``).

    The file itself is kept so the review overlay can render its pages.
    Row-level security: tenant-scoped (``v6z64_idp_documents``).
    """

    __tablename__ = "idp_documents"
    __table_args__ = (
        CheckConstraint("status IN ('processed', 'review', 'approved', 'rejected')", name="ck_idp_documents_status"),
        Index("ix_idp_documents_tenant_status_created", "tenant_id", "status", "created_at"),
        Index("ix_idp_documents_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/pdf")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processed")
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    corrections: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    review_reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
