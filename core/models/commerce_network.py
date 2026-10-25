# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A merchant's listing in the AgenticOrg shopping network ("Sell everywhere").

Row-level security: tenant-scoped with ENABLE + FORCE RLS and a tenant policy;
a SELECT-only platform-context policy lets the public directory and the network
checkout gateway resolve a slug across tenants (``v6z95_network_listings``).
Writes always run in the owning tenant's session.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

LISTING_STATUSES: tuple[str, ...] = ("listed", "paused")


class CommerceNetworkListingRow(BaseModel):
    __tablename__ = "commerce_network_listings"
    __table_args__ = (
        Index("uq_commerce_network_listings_scope", "tenant_id", "merchant_id", "seller_agent_id", unique=True),
        Index("uq_commerce_network_listings_slug", "slug", unique=True),
        Index("ix_commerce_network_listings_status", "status", "updated_at"),
        CheckConstraint("status IN ('listed', 'paused')", name="ck_commerce_network_listings_status"),
        CheckConstraint(
            "slug ~ '^[a-z0-9](?:[a-z0-9-]{1,38})[a-z0-9]$'",
            name="ck_commerce_network_listings_slug",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    slug: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    serving_host: Mapped[str | None] = mapped_column(String(253), nullable=True)
    subdomain_host: Mapped[str | None] = mapped_column(String(253), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="listed")
    terms_version: Mapped[str] = mapped_column(String(32), nullable=False)
    opted_in_by: Mapped[str] = mapped_column(String(255), nullable=False)
    opted_in_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
