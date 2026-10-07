# SPDX-License-Identifier: Apache-2.0
"""One movement on one account for transaction intelligence (``core/txn/records.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class TxnRecord(BaseModel):
    """A transaction record, idempotent under its reference. Row-level security: tenant-scoped, forced."""

    __tablename__ = "txn_records"
    __table_args__ = (
        Index("ux_txn_records_tenant_ref", "tenant_id", "record_ref", unique=True),
        Index("ix_txn_records_tenant_account_booked", "tenant_id", "account", "booked_at"),
        Index("ix_txn_records_tenant_customer", "tenant_id", "customer_ref"),
        Index("ix_txn_records_tenant_counterparty", "tenant_id", "counterparty"),
        Index("ix_txn_records_tenant_booked", "tenant_id", "booked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    record_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    counterparty: Mapped[str | None] = mapped_column(String(64), nullable=True)
    counterparty_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Any] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="other")
    branch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    booked_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="api")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
