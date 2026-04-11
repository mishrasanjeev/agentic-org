"""Feature flag model — internal feature-flag system.

Added in v4.6.0 to close the enterprise readiness gap around safe rollouts.
Each flag is either:
  - Global (tenant_id IS NULL) — default for every tenant
  - Tenant-scoped override (tenant_id IS NOT NULL)

The effective value for (tenant, flag) is: the tenant row if present, else
the global row, else "disabled". See ``core/feature_flags.py`` for the
evaluator including percentage rollouts.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class FeatureFlag(BaseModel):
    __tablename__ = "feature_flags"
    __table_args__ = (
        UniqueConstraint("tenant_id", "flag_key", name="uq_flag_tenant_key"),
        CheckConstraint(
            "rollout_percentage BETWEEN 0 AND 100",
            name="ck_flag_rollout_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    flag_key: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rollout_percentage: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
