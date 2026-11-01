# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights: one fact per agent run, the daily rollup and its reconciliation state.

Migration ``v6z106_insights_foundation`` creates these tables. An empty
database is built from these models, so every default, CHECK constraint and
index of the migration is declared here with the same text and name, and
``run_facts`` is declared range partitioned by ``started_at`` (the migration
then creates its monthly partitions on fresh and upgraded databases alike).
Every table, and every partition, is tenant scoped under forced row-level
security.

``run_facts`` holds no subject identifiers (no user id, email or message
text): agent, run and cost dimensions only.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Double,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

RUN_FACT_STATUSES: tuple[str, ...] = (
    "completed",
    "failed",
    "hitl_triggered",
    "limit_exceeded",
    "refused",
    "paused",
    "rejected",
)
RUN_FACT_ENTRYPOINTS: tuple[str, ...] = (
    "agents",
    "channel",
    "chat",
    "a2a",
    "mcp",
    "voice",
    "hitl_resume",
    "debug",
    "workflow",
    "sales",
    "customer_agent",
    "rpa",
)
RUN_FACT_ENVIRONMENTS: tuple[str, ...] = ("production", "shadow", "sandbox")
DATA_QUALITY_VALUES: tuple[str, ...] = ("measured", "seeded")
ROLLUP_STATE_STATUSES: tuple[str, ...] = ("rolled", "reconciled", "mismatch")
# Twelve latency bucket counts (bounds in core/insights/buckets.py).
LATENCY_BUCKET_COUNT = 12


def _in_list(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


# The monthly partitions of run_facts: October 2026 to December 2028 plus a
# default partition, so a fact past the horizon is kept, never lost. Created by
# the migration only (strict runtimes forbid DDL); a later migration adds months.
RUN_FACTS_TABLE = "run_facts"
RUN_FACTS_FIRST_MONTH = (2026, 10)
RUN_FACTS_LAST_MONTH = (2028, 12)
RUN_FACTS_DEFAULT_PARTITION = f"{RUN_FACTS_TABLE}_default"


def _months(first: tuple[int, int], last: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    year, month = first
    out = []
    while (year, month) <= last:
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return tuple(out)


RUN_FACTS_STATIC_MONTHS: tuple[tuple[int, int], ...] = _months(RUN_FACTS_FIRST_MONTH, RUN_FACTS_LAST_MONTH)


def run_facts_partition_name(year: int, month: int) -> str:
    """``run_facts_y2026m10``."""
    return f"{RUN_FACTS_TABLE}_y{year:04d}m{month:02d}"


RUN_FACTS_STATIC_PARTITIONS: tuple[str, ...] = (
    *(run_facts_partition_name(y, m) for y, m in RUN_FACTS_STATIC_MONTHS),
    RUN_FACTS_DEFAULT_PARTITION,
)


def _false() -> Any:
    return text("false")


def _zero() -> Any:
    return text("0")


class RunFact(BaseModel):
    """One agent run's outcome, usage and cost. Partitioned by month on ``started_at``. Forced RLS.

    Written once per run (``INSERT ... ON CONFLICT DO NOTHING`` on
    ``ux_run_facts_tenant_run_key``); never updated.
    """

    __tablename__ = "run_facts"
    __table_args__ = (
        # A partitioned table's keys must contain the partition key.
        PrimaryKeyConstraint("id", "started_at", name="pk_run_facts"),
        CheckConstraint(f"status IN ({_in_list(RUN_FACT_STATUSES)})", name="ck_run_facts_status"),
        CheckConstraint(f"entrypoint IN ({_in_list(RUN_FACT_ENTRYPOINTS)})", name="ck_run_facts_entrypoint"),
        CheckConstraint(f"data_quality IN ({_in_list(DATA_QUALITY_VALUES)})", name="ck_run_facts_data_quality"),
        CheckConstraint(f"environment IN ({_in_list(RUN_FACT_ENVIRONMENTS)})", name="ck_run_facts_environment"),
        Index("ux_run_facts_tenant_run_key", "tenant_id", "run_key", "started_at", unique=True),
        # Also covers the tenant_id foreign key.
        Index("ix_run_facts_tenant_started", "tenant_id", "started_at"),
        Index("ix_run_facts_tenant_agent_started", "tenant_id", "agent_id", "started_at"),
        {"postgresql_partition_by": "RANGE (started_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    run_key: Mapped[str] = mapped_column(String(160), nullable=False)
    entrypoint: Mapped[str] = mapped_column(String(24), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(50), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    step_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    task_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    use_case: Mapped[str | None] = mapped_column(String(100), nullable=True)
    environment: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'production'"), default="production"
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Double, nullable=True)
    confidence_floor: Mapped[float | None] = mapped_column(Double, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hitl_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=_false(), default=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    tokens_cached: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    tokens_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    # The runtime-reported cost: the same number agent_cost_ledger receives.
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, server_default=_zero(), default=Decimal("0")
    )
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Exception class name or error code; never the message.
    error_class: Mapped[str | None] = mapped_column(String(100), nullable=True)
    outcome_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=_false(), default=False)
    data_quality: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'measured'"), default="measured"
    )
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class InsightsDaily(BaseModel):
    """Run facts summed per tenant-local day and dimension combination; rebuildable. Forced RLS."""

    __tablename__ = "insights_daily"
    __table_args__ = (
        CheckConstraint(
            f"cardinality(latency_buckets) = {LATENCY_BUCKET_COUNT}", name="ck_insights_daily_latency_buckets"
        ),
        Index("ux_insights_daily_tenant_day_dims", "tenant_id", "day", "dims_hash", unique=True),
        Index("ix_insights_daily_tenant_day", "tenant_id", "day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)  # tenant-local day
    dims_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex of the dimension tuple
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entrypoint: Mapped[str] = mapped_column(String(24), nullable=False)
    task_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    data_quality: Mapped[str] = mapped_column(String(12), nullable=False)
    runs: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    hitl_required: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    outcome_validated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    tokens_in: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=_zero(), default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=_zero(), default=0)
    tokens_cached: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=_zero(), default=0)
    tokens_total: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=_zero(), default=0)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(16, 6), nullable=False, server_default=_zero(), default=Decimal("0")
    )
    duration_ms_sum: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=_zero(), default=0)
    duration_n: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    latency_buckets: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    confidence_sum: Mapped[float] = mapped_column(Double, nullable=False, server_default=_zero(), default=0.0)
    confidence_n: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    below_floor: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    computed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class InsightsRollupState(BaseModel):
    """Per tenant-day rollup and reconciliation state. Forced RLS."""

    __tablename__ = "insights_rollup_state"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in_list(ROLLUP_STATE_STATUSES)})", name="ck_insights_rollup_state_status"),
        Index("ux_insights_rollup_state_tenant_day", "tenant_id", "day", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    rolled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    source_runs: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    rolled_runs: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_zero(), default=0)
    source_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=_zero(), default=0)
    rolled_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=_zero(), default=0)
    source_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(16, 6), nullable=False, server_default=_zero(), default=Decimal("0")
    )
    rolled_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(16, 6), nullable=False, server_default=_zero(), default=Decimal("0")
    )
    detail: Mapped[str | None] = mapped_column(String(200), nullable=True)
