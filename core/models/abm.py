"""ABM (Account-Based Marketing) ORM models."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel, TenantMixin, TimestampMixin


class ABMAccount(BaseModel, TenantMixin, TimestampMixin):
    __tablename__ = "abm_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(200))
    tier: Mapped[str] = mapped_column(String(10), server_default="3")
    industry: Mapped[str | None] = mapped_column(String(100))
    revenue: Mapped[str | None] = mapped_column(String(50))
    employee_count: Mapped[str | None] = mapped_column(String(50))
    intent_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="0")
    engagement_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="0")
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    contacts: Mapped[dict] = mapped_column(JSONB, server_default="[]")
    metadata_: Mapped[dict] = mapped_column("metadata_", JSONB, server_default="{}")


class ABMCampaign(BaseModel, TenantMixin, TimestampMixin):
    __tablename__ = "abm_campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("abm_accounts.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[str | None] = mapped_column(String(50))
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(20), server_default="draft")
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    results: Mapped[dict] = mapped_column(JSONB, server_default="{}")
