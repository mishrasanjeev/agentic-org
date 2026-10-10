# SPDX-License-Identifier: Apache-2.0
"""Spend invoices and reconciliations: provider invoices, their lines, reconciliation runs and their items.

Written by ``core/spend/invoices.py`` and ``core/spend/reconcile.py``
(migration ``v6z82_spend_reconciliation``). An empty database is built from
these models, so every default, CHECK constraint, partial index and composite
foreign key of the migration is declared here with the same text and name.
Every table is tenant scoped under forced row-level security. Rows are never
deleted: a re-imported invoice supersedes the earlier one and a re-run
supersedes the earlier reconciliation, both kept, because the audit rows of
these writes carry amounts and are read beside them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

_USAGE_TYPES_SQL = "'llm_tokens','embedding_tokens','ocr_pages','speech_minutes','tool_calls','storage','gpu_hours'"
_CARD_UNITS_SQL = (
    "'1m_input_tokens','1m_output_tokens','1m_cached_input_tokens','1m_tokens','1m_embedding_tokens',"
    "'ocr_page','audio_minute','call','gb_month','gb_day','gpu_node_hour'"
)


def _json_list() -> Any:
    return text("'[]'::jsonb")


class SpendInvoice(BaseModel):
    """A provider's invoice or billing export for one billing month; superseded, never deleted. Forced RLS."""

    __tablename__ = "spend_invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="ux_spend_invoices_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "superseded_by"],
            ["spend_invoices.tenant_id", "spend_invoices.id"],
            name="fk_spend_invoices_superseded_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint("EXTRACT(DAY FROM period_start) = 1", name="ck_spend_invoices_period"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_spend_invoices_currency"),
        CheckConstraint("source IN ('csv','json')", name="ck_spend_invoices_source"),
        CheckConstraint("status IN ('current','superseded')", name="ck_spend_invoices_status"),
        Index(
            "ux_spend_invoices_current",
            "tenant_id",
            "provider",
            "period_start",
            "invoice_ref",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
        Index("ix_spend_invoices_superseded_by", "tenant_id", "superseded_by"),
        Index("ix_spend_invoices_tenant_period", "tenant_id", "period_start", "provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)  # first day of the provider's billing month
    invoice_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)  # every line
    usage_amount: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)  # usage lines only
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    file_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'current'"), default="current")
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    imported_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendInvoiceLine(BaseModel):
    """One line of an invoice: usage (by usage type, SKU and unit), a credit, tax, a fee or a commitment charge."""

    __tablename__ = "spend_invoice_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            ["spend_invoices.tenant_id", "spend_invoices.id"],
            name="fk_spend_invoice_lines_invoice",
            ondelete="CASCADE",
        ),
        CheckConstraint("line_kind IN ('usage','credit','tax','fee','commitment')", name="ck_spend_invoice_lines_kind"),
        CheckConstraint(
            "line_kind <> 'usage' OR (usage_type IS NOT NULL AND amount >= 0)", name="ck_spend_invoice_lines_usage"
        ),
        CheckConstraint("line_kind <> 'credit' OR amount <= 0", name="ck_spend_invoice_lines_credit"),
        CheckConstraint(
            f"usage_type IS NULL OR usage_type IN ({_USAGE_TYPES_SQL})", name="ck_spend_invoice_lines_usage_type"
        ),
        CheckConstraint(f"unit IS NULL OR unit IN ({_CARD_UNITS_SQL})", name="ck_spend_invoice_lines_unit"),
        Index("ux_spend_invoice_lines_invoice_line", "tenant_id", "invoice_id", "line_no", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    line_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'usage'"), default="usage")
    usage_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # required on usage lines
    model_sku: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)  # a card unit; NULL = every unit
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)  # in ``unit``
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    usage_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # a billing date (daily exports)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendReconciliation(BaseModel):
    """One reconciliation run of a provider's billing month: invoice against metered usage, two figures."""

    __tablename__ = "spend_reconciliations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="ux_spend_reconciliations_tenant_id"),
        CheckConstraint(
            "status IN ('within_tolerance','needs_review','accepted')", name="ck_spend_reconciliations_status"
        ),
        CheckConstraint("(accepted_at IS NULL) = (accept_reason IS NULL)", name="ck_spend_reconciliations_accepted"),
        Index("ix_spend_reconciliations_tenant_period", "tenant_id", "period_start", "provider", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    billing_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    invoice_amount: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)  # usage lines
    non_usage_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 10), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    # Records as stored plus adjustments; NULL when a group is unpriced, unconverted or not convertible.
    stored_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    # Re-priced at the cards in force on each billing day, as known at run time.
    repriced_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    adjustments_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 10), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    stored_variance_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    stored_variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    repriced_variance_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    repriced_variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    tolerance_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("1.00"), default=Decimal("1.00")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    needs_review_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    unpriced_quantity_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    unknown_account_records: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    platform_billed_amount_inr: Mapped[Decimal] = mapped_column(
        Numeric(28, 10), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    invoice_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=_json_list(), default=list)
    card_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default=text("'{}'"), default=list
    )
    # [{currency, on, rate_date, rate_to_inr, updated_at}]: every FX row the run used (or found missing).
    fx_rows: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=_json_list(), default=list)
    # Cards and FX rows entered or changed after the period or after the newest invoice import.
    retroactive: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=_json_list(), default=list)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    accepted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    accept_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    run_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendReconciliationItem(BaseModel):
    """One compared key of a run (usage type, SKU, unit, maybe a day), or a non-usage invoice line."""

    __tablename__ = "spend_reconciliation_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "reconciliation_id"],
            ["spend_reconciliations.tenant_id", "spend_reconciliations.id"],
            name="fk_spend_reconciliation_items_run",
            ondelete="CASCADE",
        ),
        CheckConstraint("item_kind IN ('usage','non_usage_line')", name="ck_spend_reconciliation_items_kind"),
        CheckConstraint(
            "status IN ('within_tolerance','needs_review','accepted','informational') "
            "AND ((item_kind = 'usage') = (status <> 'informational'))",
            name="ck_spend_reconciliation_items_status",
        ),
        CheckConstraint(
            "(status = 'accepted') = (accepted_at IS NOT NULL AND accept_reason IS NOT NULL)",
            name="ck_spend_reconciliation_items_accepted",
        ),
        Index("ix_spend_reconciliation_items_reconciliation", "tenant_id", "reconciliation_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reconciliation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'usage'"), default="usage")
    line_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    usage_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_sku: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    usage_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    invoice_quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    metered_quantity: Mapped[Decimal] = mapped_column(
        Numeric(24, 6), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    invoice_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 10), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    stored_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    repriced_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    # [{kind: tier_true_up | commitment_overage, amount, contract_key | commitment_id}]
    adjustments: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=_json_list(), default=list)
    stored_variance_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    stored_variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    repriced_variance_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    repriced_variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    fx_converted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    unpriced_quantity: Mapped[Decimal] = mapped_column(
        Numeric(24, 6), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    unconverted_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 10), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    # [{id, role: stored | repriced, unit_price, currency, source, created_at, updated_at}]
    cards: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=_json_list(), default=list)
    fx: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=_json_list(), default=list)
    invoice_line_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=_json_list(), default=list
    )
    days: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=_json_list(), default=list)
    accepted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    accept_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    carried_from: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
