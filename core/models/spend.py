# SPDX-License-Identifier: Apache-2.0
"""Spend reference data: the organisation tree, source mappings, rate cards, model aliases, commitments, FX rates.

Written by ``core/spend/`` (migration ``v6z79_spend_reference``). An empty
database is built from these models, not from the migration, so every
default, CHECK constraint, partial index and composite foreign key of the
migration is declared here with the same text and name. Every table is
tenant scoped under forced row-level security. Spend-to-spend foreign keys
are composite on ``(tenant_id, id)``, so a row can never point at another
tenant's row even though foreign-key checks bypass row-level security.
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
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

_USAGE_TYPES_SQL = "'llm_tokens','embedding_tokens','ocr_pages','speech_minutes','tool_calls','storage','gpu_hours'"


class SpendOrgNode(BaseModel):
    """A node of the organisation tree (group, business unit, department, team, cost centre). Forced RLS."""

    __tablename__ = "spend_org_nodes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="ux_spend_org_nodes_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["spend_org_nodes.tenant_id", "spend_org_nodes.id"],
            name="fk_spend_org_nodes_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "kind IN ('group','business_unit','department','team','cost_centre')", name="ck_spend_org_nodes_kind"
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_spend_org_nodes_parent"),
        CheckConstraint("code <> ''", name="ck_spend_org_nodes_code"),
        Index("ux_spend_org_nodes_tenant_code", "tenant_id", "code", unique=True),
        Index("ix_spend_org_nodes_parent", "tenant_id", "parent_id"),
        Index("ix_spend_org_nodes_owner", "owner_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)  # the organisation's own code, upper-case
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendSourceMapping(BaseModel):
    """A spend source (agent, application, workflow, legacy label) mapped to a node and labels. Forced RLS."""

    __tablename__ = "spend_source_mappings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "org_node_id"],
            ["spend_org_nodes.tenant_id", "spend_org_nodes.id"],
            name="fk_spend_source_mappings_org_node",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_type IN ('agent','application','workflow','cost_center','department')",
            name="ck_spend_source_mappings_source_type",
        ),
        CheckConstraint(
            "org_node_id IS NOT NULL OR product_line IS NOT NULL OR use_case IS NOT NULL",
            name="ck_spend_source_mappings_target",
        ),
        Index("ux_spend_source_mappings_tenant_source", "tenant_id", "source_type", "source_ref", unique=True),
        Index("ix_spend_source_mappings_org_node", "tenant_id", "org_node_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    org_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    product_line: Mapped[str | None] = mapped_column(String(64), nullable=True)
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendRateCard(BaseModel):
    """An effective-dated price for (provider, usage type, model or default, unit, source). Forced RLS."""

    __tablename__ = "spend_rate_cards"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="ux_spend_rate_cards_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "replaces_id"],
            ["spend_rate_cards.tenant_id", "spend_rate_cards.id"],
            name="fk_spend_rate_cards_replaces",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"usage_type IN ({_USAGE_TYPES_SQL})", name="ck_spend_rate_cards_usage_type"),
        CheckConstraint(
            "(usage_type = 'llm_tokens' AND unit IN "
            "('1m_input_tokens','1m_output_tokens','1m_cached_input_tokens','1m_tokens')) "
            "OR (usage_type = 'embedding_tokens' AND unit = '1m_embedding_tokens') "
            "OR (usage_type = 'ocr_pages' AND unit = 'ocr_page') "
            "OR (usage_type = 'speech_minutes' AND unit = 'audio_minute') "
            "OR (usage_type = 'tool_calls' AND unit = 'call') "
            "OR (usage_type = 'storage' AND unit IN ('gb_month','gb_day')) "
            "OR (usage_type = 'gpu_hours' AND unit = 'gpu_node_hour')",
            name="ck_spend_rate_cards_unit",
        ),
        CheckConstraint(
            "unit_price >= 0 AND (cached_unit_price IS NULL OR cached_unit_price >= 0)",
            name="ck_spend_rate_cards_price",
        ),
        CheckConstraint("cached_unit_price IS NULL OR unit = '1m_input_tokens'", name="ck_spend_rate_cards_cached"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_spend_rate_cards_currency"),
        CheckConstraint("batch_discount_pct >= 0 AND batch_discount_pct <= 100", name="ck_spend_rate_cards_batch"),
        CheckConstraint("tier_mode IN ('graduated','all_units')", name="ck_spend_rate_cards_tier_mode"),
        CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_spend_rate_cards_dates"),
        CheckConstraint("source IN ('list','contract')", name="ck_spend_rate_cards_source"),
        CheckConstraint("status IN ('active','retired')", name="ck_spend_rate_cards_status"),
        CheckConstraint("(status = 'retired') = (retired_at IS NOT NULL)", name="ck_spend_rate_cards_retired"),
        Index(
            "ux_spend_rate_cards_key_from",
            "tenant_id",
            "provider",
            "usage_type",
            "model_sku",
            "unit",
            "source",
            "effective_from",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_spend_rate_cards_lookup", "tenant_id", "provider", "usage_type", "status", "effective_from"),
        Index("ix_spend_rate_cards_replaces", "tenant_id", "replaces_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # '' = the provider-wide default for the usage type
    model_sku: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    cached_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    batch_discount_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    volume_tiers: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    tier_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'graduated'"), default="graduated"
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)  # a billing date of the provider
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)  # exclusive
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"), default="active")
    replaces_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    reference: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("''"), default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendModelAlias(BaseModel):
    """A model name as called, mapped to the SKU cards and invoices use. Forced RLS."""

    __tablename__ = "spend_model_aliases"
    __table_args__ = (
        CheckConstraint(
            "alias <> model_sku AND alias <> '' AND model_sku <> ''", name="ck_spend_model_aliases_distinct"
        ),
        Index("ux_spend_model_aliases_tenant_alias", "tenant_id", "provider", "alias", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    alias: Mapped[str] = mapped_column(String(128), nullable=False)  # lower-cased model name as called
    model_sku: Mapped[str] = mapped_column(String(128), nullable=False)  # lower-cased SKU
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendCommitment(BaseModel):
    """A committed or prepaid volume (a quantity in card units, or money) over a billing period. Forced RLS."""

    __tablename__ = "spend_commitments"
    __table_args__ = (
        CheckConstraint("kind IN ('quantity','money')", name="ck_spend_commitments_kind"),
        CheckConstraint(
            "(kind = 'quantity' AND committed_quantity > 0 AND usage_type IS NOT NULL AND unit IS NOT NULL "
            "AND unit <> 'gb_month') OR (kind = 'money' AND committed_amount > 0 AND currency IS NOT NULL)",
            name="ck_spend_commitments_shape",
        ),
        CheckConstraint(
            f"usage_type IS NULL OR usage_type IN ({_USAGE_TYPES_SQL})", name="ck_spend_commitments_usage_type"
        ),
        CheckConstraint(
            "(overage_unit_price IS NULL) = (overage_currency IS NULL) "
            "AND (overage_unit_price IS NULL OR kind = 'quantity')",
            name="ck_spend_commitments_overage",
        ),
        CheckConstraint("period_end > period_start", name="ck_spend_commitments_period"),
        CheckConstraint(
            "drawn_quantity >= 0 AND drawn_amount >= 0 AND undrawn_records >= 0", name="ck_spend_commitments_drawn"
        ),
        CheckConstraint("status IN ('active','closed')", name="ck_spend_commitments_status"),
        Index("ix_spend_commitments_lookup", "tenant_id", "provider", "status", "period_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_sku: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)  # a canonical card unit; quantity only
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    committed_quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)  # in card units
    committed_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)  # billing dates of the provider
    period_end: Mapped[date] = mapped_column(Date, nullable=False)  # exclusive
    overage_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)  # per card unit
    overage_currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    drawn_quantity: Mapped[Decimal] = mapped_column(
        Numeric(28, 6), nullable=False, server_default=text("0"), default=Decimal("0")
    )  # in record units
    drawn_amount: Mapped[Decimal] = mapped_column(
        Numeric(28, 10), nullable=False, server_default=text("0"), default=Decimal("0")
    )  # in the commitment currency
    undrawn_records: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)
    recomputed_through: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    recomputed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    needs_full_recompute: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"), default="active")
    reference: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("''"), default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class SpendFxRate(BaseModel):
    """The reference rate of one currency to INR on one reporting date. Forced RLS."""

    __tablename__ = "spend_fx_rates"
    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$' AND currency <> 'INR'", name="ck_spend_fx_rates_currency"),
        CheckConstraint("rate_to_inr > 0", name="ck_spend_fx_rates_rate"),
        CheckConstraint("source IN ('reference','manual','import')", name="ck_spend_fx_rates_source"),
        Index("ux_spend_fx_rates_tenant_currency_date", "tenant_id", "currency", "rate_date", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)  # a reporting date
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    rate_to_inr: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'manual'"), default="manual")
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"), default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
