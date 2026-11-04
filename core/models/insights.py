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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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


class InsightsBiLogin(BaseModel):
    """A tenant's BI database login (``v6z108_insights_bi``): the role name and when it was created or revoked.

    Written only by ``public.insights_bi_create_login`` and
    ``public.insights_bi_revoke_login``; the application reads it. Forced RLS.
    No password or verifier is kept here.
    """

    __tablename__ = "insights_bi_logins"
    __table_args__ = (
        UniqueConstraint("role_name", name="uq_insights_bi_logins_role_name"),
        Index("ix_insights_bi_logins_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    role_name: Mapped[str] = mapped_column(String(63), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


# ---------------------------------------------------------------- money saved and alerts (v6z109)

VALUE_SOURCES: tuple[str, ...] = ("seeded", "tenant")
VALUE_CURRENCIES: tuple[str, ...] = ("INR", "USD")
ALERT_SEVERITIES: tuple[str, ...] = ("watch", "act")
ALERT_STATES: tuple[str, ...] = ("open", "acked", "resolved")
EXPORT_KINDS: tuple[str, ...] = (
    "metric",
    "explorer",
    "run_bundle",
    "view_pdf",
    "finance_pack",
    "evidence_pack",
    "partner_pack",
    "view_pptx",
)
EXPORT_STATUSES: tuple[str, ...] = ("queued", "running", "ready", "failed", "expired")
EXPORT_FORMATS: tuple[str, ...] = ("csv", "xlsx", "json", "pdf", "zip", "pptx", "parquet")


class ValueBaseline(BaseModel):
    """Minutes of human work one completed task of an agent type replaces, and who would do it. Forced RLS.

    ``task_type`` ``*`` covers every task of the agent type; a specific task type wins over it.
    ``source`` says where the figure came from (``seeded`` defaults or the ``tenant``); savings
    count only once a tenant administrator confirmed it (``confirmed_at``).
    """

    __tablename__ = "value_baselines"
    __table_args__ = (
        CheckConstraint(f"source IN ({_in_list(VALUE_SOURCES)})", name="ck_value_baselines_source"),
        CheckConstraint("baseline_minutes > 0 AND baseline_minutes <= 1440", name="ck_value_baselines_minutes"),
        CheckConstraint("setup_cost IS NULL OR setup_cost >= 0", name="ck_value_baselines_setup_cost"),
        UniqueConstraint("tenant_id", "agent_type", "task_type", name="uq_value_baselines_tenant_type_task"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(100), nullable=False)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False, server_default=text("'*'"), default="*")
    baseline_minutes: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    setup_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class ValueRate(BaseModel):
    """A role's loaded hourly cost, with where the figure came from. Forced RLS.

    Role ``reviewer`` prices the time people spend deciding approvals. ``citation`` names the
    source of a seeded default (publisher, series, period); savings count only once a tenant
    administrator confirmed the rate.
    """

    __tablename__ = "value_rates"
    __table_args__ = (
        CheckConstraint(f"source IN ({_in_list(VALUE_SOURCES)})", name="ck_value_rates_source"),
        CheckConstraint(f"currency IN ({_in_list(VALUE_CURRENCIES)})", name="ck_value_rates_currency"),
        CheckConstraint("hourly_cost >= 0", name="ck_value_rates_hourly_cost"),
        UniqueConstraint("tenant_id", "role", name="uq_value_rates_tenant_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    hourly_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    citation: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class InsightsAlert(BaseModel):
    """An Insights alert: one per (rule, entity) while it is not resolved. Forced RLS.

    Opened by the alert sweep (``core/insights/alerts.py``), refreshed while the condition
    holds (``last_seen_at``), resolved when it clears; a person acknowledges it in place.
    ``notified_at`` records the one email an ``act`` alert sends.
    """

    __tablename__ = "insights_alerts"
    __table_args__ = (
        CheckConstraint(f"severity IN ({_in_list(ALERT_SEVERITIES)})", name="ck_insights_alerts_severity"),
        CheckConstraint(f"state IN ({_in_list(ALERT_STATES)})", name="ck_insights_alerts_state"),
        Index(
            "ux_insights_alerts_live",
            "tenant_id",
            "rule_key",
            "entity_ref",
            unique=True,
            postgresql_where=text("state <> 'resolved'"),
        ),
        Index("ix_insights_alerts_tenant_state", "tenant_id", "state", "fired_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_ref: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("''"), default="")
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    owner_role: Mapped[str] = mapped_column(String(32), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    deep_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    state: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'open'"), default="open")
    fired_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    acked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class InsightsExport(BaseModel):
    """One background export job and the file it made (migration v6z110). Forced RLS.

    ``params`` holds what to export and the caller's role, domains and admin flag as the server read them when
    the job was made; the worker builds the file with exactly that scope. A ready file lives in the export bucket
    under ``storage_key`` until ``expires_at``; downloads go through the API (never a signed URL).
    """

    __tablename__ = "insights_exports"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_in_list(EXPORT_KINDS)})", name="ck_insights_exports_kind"),
        CheckConstraint(f"status IN ({_in_list(EXPORT_STATUSES)})", name="ck_insights_exports_status"),
        CheckConstraint(f"format IN ({_in_list(EXPORT_FORMATS)})", name="ck_insights_exports_format"),
        Index("ix_insights_exports_tenant_user_created", "tenant_id", "user_id", text("created_at DESC")),
        Index(
            "ix_insights_exports_expiry",
            "expires_at",
            postgresql_where=text("status IN ('ready','failed','queued','running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    include_pii: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'queued'"), default="queued")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    signature: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class SavedView(BaseModel):
    """A named filter set on one Insights landing view or explorer (``view_key`` is the view, or ``explore:<key>``).

    ``filters`` is the URL query the page already uses. Owned by one person; ``shared`` shows it to everyone in the
    tenant who may open that view or explorer. Forced RLS.
    """

    __tablename__ = "saved_views"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "view_key", "name", name="uq_saved_views_owner_name"),
        Index("ix_saved_views_tenant_view", "tenant_id", "view_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    view_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class InsightsUserState(BaseModel):
    """When a person last opened Insights (the "what changed since you last looked" strip). Forced RLS."""

    __tablename__ = "insights_user_state"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


SECURITY_EVENT_KINDS: tuple[str, ...] = (
    "login_failed",
    "rate_limited",
    "scope_denied",
    "session_revoked",
    "api_key_used",
    "webhook_signature_failed",
)


class GuardrailEvent(BaseModel):
    """Per tenant and UTC day, how many guardrail outcomes a stage, detector, action and mode produced. Forced RLS."""

    __tablename__ = "guardrail_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "day", "stage", "detector", "action", "mode", name="uq_guardrail_events_day"),
        CheckConstraint("count >= 0", name="ck_guardrail_events_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    detector: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SecurityEventDaily(BaseModel):
    """Per tenant and UTC day, how many of each security event kind happened. Counts only. Forced RLS."""

    __tablename__ = "security_events_daily"
    __table_args__ = (
        UniqueConstraint("tenant_id", "day", "event_kind", name="uq_security_events_daily_day"),
        CheckConstraint(f"event_kind IN ({_in_list(SECURITY_EVENT_KINDS)})", name="ck_security_events_daily_kind"),
        CheckConstraint("count >= 0", name="ck_security_events_daily_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


# ---------------------------------------------------------------- benchmarks (v6z117)


class InsightsBenchmarkOptin(BaseModel):
    """A tenant that chose to take part in cross-tenant benchmarks (an administrator's choice). Forced RLS."""

    __tablename__ = "insights_benchmark_optins"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    opted_in_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    opted_in_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class InsightsBenchmark(BaseModel):
    """A published cohort figure: percentiles over at least 20 opted-in tenants. Holds no tenant id."""

    __tablename__ = "insights_benchmarks"
    __table_args__ = (
        UniqueConstraint("cohort", "metric", name="uq_insights_benchmarks_cohort_metric"),
        CheckConstraint("tenants >= 20", name="ck_insights_benchmarks_tenants"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cohort: Mapped[str] = mapped_column(String(64), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    window_from: Mapped[date] = mapped_column(Date, nullable=False)
    window_to: Mapped[date] = mapped_column(Date, nullable=False)
    p25: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    p50: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    p75: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    tenants: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
