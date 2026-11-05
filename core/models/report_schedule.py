"""Report Schedule ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, func, text
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
    # Who made the schedule (migration v6z111); an Insights report runs with this user's access.
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


RUN_STATUSES: tuple[str, ...] = ("delivered", "generated", "failed", "blocked")
RUN_TRIGGERS: tuple[str, ...] = ("schedule", "manual")


class ReportScheduleRun(BaseModel):
    """One run of a report schedule and how it ended (migration v6z111). Forced RLS."""

    __tablename__ = "report_schedule_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ",".join(f"'{s}'" for s in RUN_STATUSES) + ")", name="ck_report_schedule_runs_status"
        ),
        CheckConstraint(
            "trigger IN (" + ",".join(f"'{s}'" for s in RUN_TRIGGERS) + ")", name="ck_report_schedule_runs_trigger"
        ),
        Index("ix_report_schedule_runs_schedule", "tenant_id", "schedule_id", text("started_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    schedule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trigger: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'schedule'"), default="schedule"
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    export_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    channels: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
