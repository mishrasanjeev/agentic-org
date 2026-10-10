# SPDX-License-Identifier: Apache-2.0
"""Spend usage: usage records, the daily rollup, meter gaps and maintenance jobs.

Written by ``core/spend/`` (migration ``v6z80_spend_usage``). An empty
database is built from these models, so every default, CHECK constraint,
partial index and composite foreign key of the migration is declared here
with the same text and name, and ``spend_usage_records`` is declared range
partitioned by ``event_time`` (the migration then creates its monthly
partitions on fresh and upgraded databases alike). Every table is tenant
scoped under forced row-level security.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    DDL,
    TIMESTAMP,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

_USAGE_TYPES_SQL = "'llm_tokens','embedding_tokens','ocr_pages','speech_minutes','tool_calls','storage','gpu_hours'"
_APPLICATIONS_SQL = (
    "'agents','chat','voice','workflows','a2a','mcp','api','console','knowledge','documents',"
    "'speech','content','txn','system'"
)
_ATTRIBUTION_PATHS_SQL = (
    "'agent_mapping','cost_centre_mapping','cost_centre_code','workflow_mapping','application_mapping',"
    "'department_mapping','department_code'"
)
_GAP_REASONS_SQL = (
    "'queue_full','spill_failed','shutdown_lost','paused','tenant_mismatch','failed_no_usage',"
    "'timeout_estimated','unpriced_tool'"
)
_JOB_KINDS_SQL = "'rebuild','backfill','restate','settle_fx','reattribute','recompute_commitments'"


def _false() -> Any:
    return text("false")


class SpendUsageRecord(BaseModel):
    """One billable quantity in one unit, priced and attributed. Partitioned by month. Forced RLS.

    Never deleted; its event fields never change. Four audited maintenance
    jobs may revise its derived fields (FX, price, attribution, commitment).
    """

    __tablename__ = "spend_usage_records"
    __table_args__ = (
        # A partitioned table's keys must contain the partition key.
        PrimaryKeyConstraint("id", "event_time", name="pk_spend_usage_records"),
        ForeignKeyConstraint(
            ["tenant_id", "rate_card_id"],
            ["spend_rate_cards.tenant_id", "spend_rate_cards.id"],
            name="fk_spend_usage_records_rate_card",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "org_node_id"],
            ["spend_org_nodes.tenant_id", "spend_org_nodes.id"],
            name="fk_spend_usage_records_org_node",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"usage_type IN ({_USAGE_TYPES_SQL})", name="ck_spend_usage_records_usage_type"),
        CheckConstraint(
            "(usage_type = 'llm_tokens' AND unit IN ('input_token','output_token','cached_input_token','token')) "
            "OR (usage_type = 'embedding_tokens' AND unit = 'embedding_token') "
            "OR (usage_type = 'ocr_pages' AND unit = 'ocr_page') "
            "OR (usage_type = 'speech_minutes' AND unit = 'audio_minute') "
            "OR (usage_type = 'tool_calls' AND unit = 'call') "
            "OR (usage_type = 'storage' AND unit = 'gb_day') "
            "OR (usage_type = 'gpu_hours' AND unit = 'gpu_node_hour')",
            name="ck_spend_usage_records_unit",
        ),
        CheckConstraint(
            "quantity >= 0 AND overage_quantity >= 0 AND overage_quantity <= quantity",
            name="ck_spend_usage_records_quantity",
        ),
        CheckConstraint("calls IN (0, 1)", name="ck_spend_usage_records_calls"),
        CheckConstraint(
            "price_source IN ('contract','list','fallback_override','fallback_list','in_house','none')",
            name="ck_spend_usage_records_price_source",
        ),
        CheckConstraint(
            "(unpriced AND amount IS NULL AND currency IS NULL AND amount_inr IS NULL AND NOT unconverted "
            "AND price_source = 'none') "
            "OR (NOT unpriced AND amount IS NOT NULL AND currency IS NOT NULL AND price_source <> 'none' "
            "AND ((unconverted AND amount_inr IS NULL) OR (NOT unconverted AND amount_inr IS NOT NULL)))",
            name="ck_spend_usage_records_priced",
        ),
        CheckConstraint(f"application IN ({_APPLICATIONS_SQL})", name="ck_spend_usage_records_application"),
        CheckConstraint(
            "(org_node_id IS NULL) = (unattributed_reason IS NOT NULL)", name="ck_spend_usage_records_attribution"
        ),
        CheckConstraint(
            "(org_node_id IS NULL) = (attribution_path IS NULL) "
            f"AND (attribution_path IS NULL OR attribution_path IN ({_ATTRIBUTION_PATHS_SQL}))",
            name="ck_spend_usage_records_attribution_path",
        ),
        CheckConstraint(
            "unattributed_reason IS NULL OR "
            "unattributed_reason IN ('no_source','no_mapping','unknown_label','inactive_node','resolver_failed')",
            name="ck_spend_usage_records_unattributed_reason",
        ),
        CheckConstraint(
            "risk_tier IS NULL OR risk_tier IN ('low','medium','high','critical')",
            name="ck_spend_usage_records_risk_tier",
        ),
        CheckConstraint(
            "billing_account IS NULL OR billing_account IN ('tenant_key','platform_key','in_house')",
            name="ck_spend_usage_records_billing_account",
        ),
        Index("ux_spend_usage_records_tenant_key", "tenant_id", "idempotency_key", "event_time", unique=True),
        Index("ix_spend_usage_records_tenant_time", "tenant_id", "event_time"),
        Index(
            "ix_spend_usage_records_rate_card",
            "tenant_id",
            "rate_card_id",
            "event_time",
            postgresql_where=text("rate_card_id IS NOT NULL"),
        ),
        Index(
            "ix_spend_usage_records_org_node",
            "tenant_id",
            "org_node_id",
            postgresql_where=text("org_node_id IS NOT NULL"),
        ),
        Index(
            "ix_spend_usage_records_fx_pending",
            "tenant_id",
            "event_time",
            postgresql_where=text("fx_estimated OR unconverted"),
        ),
        {"postgresql_partition_by": "RANGE (event_time)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)  # time-ordered (ids.time_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"), default="")
    # sha256(correlation_id)[:32]; never the raw request id
    correlation_ref: Mapped[str] = mapped_column(CHAR(32), nullable=False, server_default=text("''"), default="")
    event_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)  # the reporting zone
    billing_date: Mapped[date] = mapped_column(Date, nullable=False)  # the provider's billing zone
    usage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    calls: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"), default=0)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    rate_card_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    blend_card_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    price_source: Mapped[str] = mapped_column(String(24), nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    fx_rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_inr: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    unpriced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=_false(), default=False)
    fx_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=_false(), default=False)
    unconverted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=_false(), default=False)
    overage: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=_false(), default=False)
    overage_quantity: Mapped[Decimal] = mapped_column(
        Numeric(24, 6), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    allocated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=_false(), default=False)
    quantity_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=_false(), default=False)
    price_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=_false(), default=False)
    commitment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    org_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    business_unit_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    attribution_path: Mapped[str | None] = mapped_column(String(24), nullable=True)
    unattributed_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    product_line: Mapped[str | None] = mapped_column(String(64), nullable=True)
    use_case: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"), default="")
    application: Mapped[str] = mapped_column(String(16), nullable=False)
    region: Mapped[str | None] = mapped_column(String(8), nullable=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    initiating_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("''"), default="")
    risk_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    billing_account: Mapped[str | None] = mapped_column(String(16), nullable=True)
    allocated_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revised_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendUsageRollup(BaseModel):
    """Sums of usage records per reporting day and dimension combination; rebuildable. Forced RLS."""

    __tablename__ = "spend_usage_rollups"
    __table_args__ = (
        CheckConstraint(
            "record_count >= 0 AND call_count >= 0 AND unpriced_count >= 0 AND unconverted_count >= 0 "
            "AND fx_estimated_count >= 0 AND overage_count >= 0 AND allocated_count >= 0 AND estimated_count >= 0",
            name="ck_spend_usage_rollups_counts",
        ),
        Index("ux_spend_usage_rollups_tenant_day_dims", "tenant_id", "day", "dims_hash", unique=True),
        Index("ix_spend_usage_rollups_tenant_provider_billing", "tenant_id", "provider", "billing_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)  # the records' event date (reporting zone)
    dims_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    billing_date: Mapped[date] = mapped_column(Date, nullable=False)
    org_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    business_unit_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    attribution_path: Mapped[str | None] = mapped_column(String(24), nullable=True)
    unattributed_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    product_line: Mapped[str | None] = mapped_column(String(64), nullable=True)
    use_case: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"), default="")
    application: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    usage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    rate_card_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    price_source: Mapped[str] = mapped_column(String(24), nullable=False)
    commitment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    billing_account: Mapped[str | None] = mapped_column(String(16), nullable=True)
    region: Mapped[str | None] = mapped_column(String(8), nullable=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("''"), default="")
    risk_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(28, 6), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(28, 10), nullable=False, server_default=text("0"), default=Decimal("0")
    )  # in currency; unpriced records add 0
    amount_inr: Mapped[Decimal] = mapped_column(
        Numeric(28, 10), nullable=False, server_default=text("0"), default=Decimal("0")
    )  # unconverted records add 0
    unconverted_amount: Mapped[Decimal] = mapped_column(
        Numeric(28, 10), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    unpriced_quantity: Mapped[Decimal] = mapped_column(
        Numeric(28, 6), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    overage_quantity: Mapped[Decimal] = mapped_column(
        Numeric(28, 6), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    call_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    unpriced_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    unconverted_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    fx_estimated_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    overage_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    allocated_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    estimated_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


# The migration creates the rollup table ``WITH (fillfactor = 70)`` (its rows are updated in
# place by every write). SQLAlchemy has no table storage-parameter option, so the ORM
# bootstrap sets the same storage parameter right after it creates the table.
ROLLUP_FILLFACTOR_DDL = DDL("ALTER TABLE spend_usage_rollups SET (fillfactor = 70)")
event.listen(SpendUsageRollup.__table__, "after_create", ROLLUP_FILLFACTOR_DDL.execute_if(dialect="postgresql"))


class SpendMeterGap(BaseModel):
    """Usage that could not be metered, counted per day, usage type, reason and detail. Forced RLS."""

    __tablename__ = "spend_meter_gaps"
    __table_args__ = (
        CheckConstraint(f"usage_type IN ({_USAGE_TYPES_SQL})", name="ck_spend_meter_gaps_usage_type"),
        CheckConstraint(f"reason IN ({_GAP_REASONS_SQL})", name="ck_spend_meter_gaps_reason"),
        CheckConstraint("count >= 0", name="ck_spend_meter_gaps_count"),
        Index("ux_spend_meter_gaps_key", "tenant_id", "day", "usage_type", "reason", "detail", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    usage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    # e.g. "<provider>:<sku>" for unpriced tools; never text from a call
    detail: Mapped[str] = mapped_column(String(160), nullable=False, server_default=text("''"), default="")
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendJob(BaseModel):
    """A maintenance job (rebuild, backfill, restatement, settlement, re-attribution, recompute). Forced RLS."""

    __tablename__ = "spend_jobs"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_JOB_KINDS_SQL})", name="ck_spend_jobs_kind"),
        CheckConstraint("status IN ('queued','running','succeeded','failed')", name="ck_spend_jobs_status"),
        Index(
            "ux_spend_jobs_active",
            "tenant_id",
            "kind",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        Index("ix_spend_jobs_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'queued'"), default="queued")
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"), default="")
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
