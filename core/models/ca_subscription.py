"""CA Subscription model -- paid add-on for CA firm features.

Each tenant has at most one CA subscription. The subscription gates
access to multi-company management, GST/TDS filing agents, and
the CA firm dashboard.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class CASubscription(BaseModel):
    __tablename__ = "ca_subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_ca_sub_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )

    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="ca_pro")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="trial"
    )  # trial | active | cancelled | expired
    max_clients: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    price_inr: Mapped[int] = mapped_column(Integer, nullable=False, default=4999)
    price_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=59)
    billing_cycle: Mapped[str] = mapped_column(
        String(20), nullable=False, default="monthly"
    )  # monthly | annual

    trial_ends_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    current_period_start: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    tenant = relationship("Tenant", backref="ca_subscription")
