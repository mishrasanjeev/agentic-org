# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Per-tenant daily spend of the calls the LLM router makes (UTC days).

With ``AGENTICORG_RECORDED_SPEND_CONTROLS_ENABLED``, ``core.llm.router`` adds
each priced call here and the Gemini daily caps read it back (per tenant in the
tenant's session, platform-wide through ``public.gemini_daily_ledger_spend()``).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import TIMESTAMP, Date, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class LlmDailySpend(BaseModel):
    __tablename__ = "llm_daily_spend"
    __table_args__ = (
        UniqueConstraint("tenant_id", "day", "provider", "model", name="uq_llm_daily_spend_key"),
        Index("ix_llm_daily_spend_day_provider", "day", "provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
