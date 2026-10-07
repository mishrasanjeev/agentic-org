# SPDX-License-Identifier: Apache-2.0
"""The attributed cost ledger (``core/finops/attribution.py``)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import TIMESTAMP, BigInteger, Date, Float, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class FinopsCostLedger(BaseModel):
    """One row per day, agent and attribution: tokens, cost and calls.

    Row-level security: tenant-scoped (``v6z55_finops_attribution``). The unique
    key is an expression index (the agent id coalesced), created by the migration.
    """

    __tablename__ = "finops_cost_ledger"
    __table_args__ = (
        Index("ix_finops_cost_ledger_tenant_day", "tenant_id", "period_date"),
        Index("ix_finops_cost_ledger_tenant_use_case", "tenant_id", "use_case"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    period_date: Mapped[date] = mapped_column(Date, nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    use_case: Mapped[str] = mapped_column(String(64), nullable=False)
    application: Mapped[str] = mapped_column(String(64), nullable=False)
    business_unit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
