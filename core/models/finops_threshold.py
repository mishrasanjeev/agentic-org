# SPDX-License-Identifier: Apache-2.0
"""A cost threshold with its action (``core/finops/thresholds.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, Float, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class FinopsThreshold(BaseModel):
    """One threshold: a scope, a period, an amount and an action, with its last breach.

    Row-level security: tenant-scoped (``v6z56_finops_thresholds``).
    """

    __tablename__ = "finops_thresholds"
    __table_args__ = (
        CheckConstraint(
            "scope_kind IN ('organisation', 'application', 'use_case', 'business_unit')",
            name="ck_finops_thresholds_scope",
        ),
        CheckConstraint("period IN ('daily', 'monthly')", name="ck_finops_thresholds_period"),
        CheckConstraint("action IN ('alert', 'throttle', 'suspend')", name="ck_finops_thresholds_action"),
        CheckConstraint("threshold_usd > 0", name="ck_finops_thresholds_amount"),
        Index("ux_finops_thresholds_tenant_name", "tenant_id", "name", unique=True),
        Index("ix_finops_thresholds_tenant_enabled", "tenant_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_value: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    period: Mapped[str] = mapped_column(String(8), nullable=False, default="monthly")
    threshold_usd: Mapped[float] = mapped_column(Float, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="alert")
    throttle_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="email")
    last_breach_period: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_breach_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_breach_spend_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    lifted_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
