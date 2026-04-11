"""Invoice model — persistent record of generated billing invoices.

Invoices are emitted monthly by the billing invoice task. Each row
points at the PDF in object storage and carries the line items as
structured JSON so we can regenerate the PDF or expose an API feed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import TIMESTAMP, Date, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class Invoice(BaseModel):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "invoice_number", name="uq_invoice_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False)

    # Period covered
    period_start: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    issue_date = mapped_column(Date, nullable=False)
    due_date = mapped_column(Date, nullable=False)

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tax: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00")
    )
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # draft | sent | paid | void
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    # Structured line items — list of {description, qty, unit_price, amount}
    line_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # PDF storage
    pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Payment provider linkage
    payment_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)  # stripe|plural
    payment_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
