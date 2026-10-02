# SPDX-License-Identifier: Apache-2.0
"""Per-model limits for the model gateway: concurrency and request rate.

One row per provider (``model`` NULL) or per model. ``max_concurrency`` caps
the model calls in flight at once, ``requests_per_minute`` the calls that may
start per minute; ``core.governance.model_gateway_limits`` enforces both in
Redis at admission time. A provider-wide row and a model row both apply to a
call on that model.

Row-level security: tenant-scoped (``v6z35_model_access_limits``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ModelLimit(BaseModel):
    __tablename__ = "model_limits"
    __table_args__ = (
        CheckConstraint(
            "max_concurrency IS NOT NULL OR requests_per_minute IS NOT NULL", name="ck_model_limits_one_limit"
        ),
        CheckConstraint("max_concurrency IS NULL OR max_concurrency >= 1", name="ck_model_limits_concurrency"),
        CheckConstraint("requests_per_minute IS NULL OR requests_per_minute >= 1", name="ck_model_limits_rate"),
        Index(
            "ux_model_limits_tenant_provider_model",
            "tenant_id",
            "provider",
            text("COALESCE(model, '')"),
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requests_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True)
