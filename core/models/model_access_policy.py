# SPDX-License-Identifier: Apache-2.0
"""Model access policies: which application, principal or business unit may use which provider or model.

A row matches a routed model call on any of its match columns (a NULL column
matches every call) and says what the match gets: ``deny`` refuses the call;
``allow`` lets it through, fenced to ``allowed_providers`` and
``allowed_models`` when those are set. ``core.governance.model_gateway``
evaluates the enabled rows in priority order after the routing policies have
chosen the provider and model; the first match decides, and a call no row
matches is allowed.

Row-level security: tenant-scoped (``v6z35_model_access_limits``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ModelAccessPolicy(BaseModel):
    __tablename__ = "model_access_policies"
    __table_args__ = (
        CheckConstraint("effect IN ('allow','deny')", name="ck_model_access_policies_effect"),
        CheckConstraint(
            "sensitivity IS NULL OR sensitivity IN ('public','internal','confidential','restricted')",
            name="ck_model_access_policies_sensitivity",
        ),
        CheckConstraint("priority >= 0", name="ck_model_access_policies_priority"),
        Index("ix_model_access_policies_tenant_enabled", "tenant_id", "enabled", "priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Match columns: NULL matches every call.
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sensitivity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    application: Mapped[str | None] = mapped_column(String(128), nullable=True)
    principal: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # What a match gets.
    effect: Mapped[str] = mapped_column(String(8), nullable=False, default="allow")
    allowed_providers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    allowed_models: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True)
