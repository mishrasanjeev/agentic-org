# SPDX-License-Identifier: Apache-2.0
"""Model routing policies: how the model gateway picks a provider and model.

A row matches requests on any of its match columns (a NULL column matches every
request) and names what the match gets: a provider, a model, a cost tier or a
weighted list of targets, the providers allowed, and whether the call must stay
in the tenant's data region.
``core.governance.model_gateway`` evaluates the enabled rows in priority order
and the first match decides.

Row-level security: tenant-scoped (``v6z34_model_routing_policies``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ModelRoutingPolicy(BaseModel):
    __tablename__ = "model_routing_policies"
    __table_args__ = (
        CheckConstraint(
            "sensitivity IS NULL OR sensitivity IN ('public','internal','confidential','restricted')",
            name="ck_model_routing_policies_sensitivity",
        ),
        CheckConstraint("tier IS NULL OR tier IN ('tier1','tier2','tier3')", name="ck_model_routing_policies_tier"),
        CheckConstraint("priority >= 0", name="ck_model_routing_policies_priority"),
        CheckConstraint(
            "max_failure_rate IS NULL OR (max_failure_rate >= 0 AND max_failure_rate <= 1)",
            name="ck_model_routing_policies_max_failure_rate",
        ),
        Index("ix_model_routing_policies_tenant_enabled", "tenant_id", "enabled", "priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Match columns: NULL matches every request.
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sensitivity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # What a match gets.
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Weighted split: a list of {provider, model, weight}; set instead of provider, model or tier.
    targets: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Cost-aware: pick the cheapest target whose observed failure rate stays under max_failure_rate.
    cost_aware: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_failure_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    allowed_providers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    in_region_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True)
