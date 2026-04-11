"""Budget alert model — notifies when spend approaches a threshold.

Added in v4.6.0 to close the enterprise readiness gap where tenants had no
proactive warning before blowing through their LLM/compute budget. A
BudgetAlert is evaluated on a schedule (daily/weekly/monthly) against
aggregated cost ledger rows for the same tenant/company/cost_center.

Row-level security: tenant-scoped.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class BudgetAlert(BaseModel):
    __tablename__ = "budget_alerts"
    __table_args__ = (
        CheckConstraint(
            "warn_at_percent BETWEEN 1 AND 100",
            name="ck_budget_warn_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
    )
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cost_centers.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)  # daily|weekly|monthly
    threshold_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    warn_at_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    notify_channels: Mapped[str] = mapped_column(
        String(255), nullable=False, default="email"
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
