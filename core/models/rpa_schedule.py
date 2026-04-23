"""RPASchedule ORM — tenant-scheduled RPA script runs.

Mirrors ReportSchedule's pattern (cron / enabled / last_run_at /
next_run_at) but decoupled from the report/delivery system because an
RPA run produces knowledge-base chunks, not reports. Keeps per-schedule
``params`` (script-specific inputs) + ``config`` (rpa-framework-level
knobs: max_items_per_section, target_quality, etc.) in dedicated JSONB
columns so the script contract is introspectable.

Quality tracking: ``last_quality_avg`` records the average quality
score of chunks emitted by the last run. Feeds the UI + alerting so
operators notice when a script starts producing sub-4.8 output after
a site redesign.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class RPASchedule(BaseModel):
    __tablename__ = "rpa_schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Display label for the schedule — must be unique per tenant for
    # idempotent re-registration.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Script key — e.g. "rbi_org_scraper", "epfo_ecr_download" —
    # resolved via rpa.scripts._registry.discover_scripts().
    script_key: Mapped[str] = mapped_column(String(100), nullable=False)
    # Cron preset ("daily"/"weekly"/...) or a 5-field cron expression.
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False, default="daily")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Script-specific inputs (e.g. {"sections": "press_releases"}).
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Framework-level knobs (e.g. {"target_quality": 4.8}).
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, index=True
    )
    last_run_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_run_chunks_published: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_run_chunks_rejected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_quality_avg: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True,
        doc="Average chunk quality score (0-5) from the most recent run; target >= 4.8.",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )
