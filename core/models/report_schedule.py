"""Report Schedule ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel, TenantMixin, TimestampMixin


class ReportSchedule(BaseModel, TenantMixin, TimestampMixin):
    __tablename__ = "report_schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Codex 2026-04-22 review: company_id hoisted from ``config`` JSONB
    # into a real nullable column + index so the list endpoint can
    # filter cheaply and two users in the same tenant who manage
    # different companies stop seeing each other's schedules.
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    recipients: Mapped[dict] = mapped_column(JSONB, server_default="[]")
    delivery_channel: Mapped[str] = mapped_column(String(20), server_default="email")
    format: Mapped[str] = mapped_column(String(10), server_default="pdf")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}")
