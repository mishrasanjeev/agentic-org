# SPDX-License-Identifier: Apache-2.0
"""Spend GPU: in-house pool node hours and each tenant's allocated share of them.

Written by ``core/spend/gpu.py`` (migration ``v6z81_spend_gpu``). An empty
database is built from these models, so every default, CHECK constraint and
index of the migration is declared here with the same text and name.

``spend_gpu_pool_hours`` is a platform table: one in-house endpoint serves
every tenant, so its node hours are a deployment cost; it holds node hours,
an aggregate token total and the node hours no tenant carries, no tenant data,
and has no ``tenant_id`` and no row-level policy. ``spend_gpu_allocations`` holds each tenant's share of a
pool hour under forced row-level security.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

WHOLE_HOUR_CHECK = "date_trunc('hour', hour_start AT TIME ZONE 'UTC') = (hour_start AT TIME ZONE 'UTC')"


class SpendGpuPoolHour(BaseModel):
    """The node hours of one in-house pool for one whole UTC hour. A platform table: no tenant, no RLS."""

    __tablename__ = "spend_gpu_pool_hours"
    __table_args__ = (
        CheckConstraint("provider IN ('ollama','vllm')", name="ck_spend_gpu_pool_hours_provider"),
        CheckConstraint("node_hours > 0 AND node_hours <= 10000", name="ck_spend_gpu_pool_hours_hours"),
        CheckConstraint("source IN ('config','metrics','manual')", name="ck_spend_gpu_pool_hours_source"),
        CheckConstraint("status IN ('pending','allocating','allocated')", name="ck_spend_gpu_pool_hours_status"),
        CheckConstraint(
            "skipped_node_hours >= 0 AND skipped_node_hours <= node_hours", name="ck_spend_gpu_pool_hours_skipped"
        ),
        CheckConstraint(WHOLE_HOUR_CHECK, name="ck_spend_gpu_pool_hours_whole_hour"),
        Index("ux_spend_gpu_pool_hours_key", "provider", "node_pool", "hour_start", unique=True),
        Index("ix_spend_gpu_pool_hours_status", "status", "hour_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    node_pool: Mapped[str] = mapped_column(String(64), nullable=False)
    # The model names this pool serves, as usage records carry them.
    models: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list)
    hour_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    node_hours: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"), default="pending")
    claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # Records created after this instant are not counted in the hour's spread.
    frozen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    total_tokens: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    tenant_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    idle: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    priced_calls_skipped: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    # The node hours no tenant record carries: an idle hour, and the share of calls a card priced above zero
    # or of tenants deleted since the hour.
    skipped_node_hours: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, server_default=text("0"), default=Decimal(0)
    )
    allocated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendGpuAllocation(BaseModel):
    """A tenant's share of one pool hour: its tokens, then its node hours and amount once written. Forced RLS."""

    __tablename__ = "spend_gpu_allocations"
    __table_args__ = (
        CheckConstraint("status IN ('frozen','written')", name="ck_spend_gpu_allocations_status"),
        CheckConstraint(
            "tokens >= 0 AND (node_hours IS NULL OR node_hours >= 0)", name="ck_spend_gpu_allocations_tokens"
        ),
        Index("ux_spend_gpu_allocations_tenant_hour", "tenant_id", "pool_hour_id", unique=True),
        Index("ix_spend_gpu_allocations_pool_hour", "pool_hour_id"),
        Index("ix_spend_gpu_allocations_tenant_hour_start", "tenant_id", "hour_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    pool_hour_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spend_gpu_pool_hours.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    node_pool: Mapped[str] = mapped_column(String(64), nullable=False)
    hour_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    tokens: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    # The tenant's share, set when its records are written.
    node_hours: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    records: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'frozen'"), default="frozen")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
