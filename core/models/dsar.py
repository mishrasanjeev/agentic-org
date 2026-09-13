"""Data-subject request (DSAR) ledger ORM model.

Schema delivered by migration ``v6z17_sessions_dsar``. Every
``POST /api/v1/dsar/*`` call persists one row; ``GET /api/v1/dsar/{id}``
reads it back. Statuses are honest: ``received`` until an operator or a
worker actually processes the request, ``completed`` / ``failed`` after.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

DSAR_REQUEST_TYPES = ("access", "erase", "export")
DSAR_STATUSES = ("received", "processing", "completed", "failed")


class DSARRequestRecord(BaseModel):
    __tablename__ = "dsar_requests"
    __table_args__ = (Index("ix_dsar_requests_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    request_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_email: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
