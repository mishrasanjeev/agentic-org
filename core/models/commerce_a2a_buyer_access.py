# SPDX-License-Identifier: Apache-2.0
"""Merchant-issued, revocable A2A buyer credentials."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class CommerceA2ABuyerAccess(BaseModel):
    __tablename__ = "commerce_a2a_buyer_access"
    __table_args__ = (
        Index("ix_commerce_a2a_buyer_access_tenant", "tenant_id"),
        Index("ix_commerce_a2a_buyer_access_scope", "tenant_id", "merchant_id", "seller_agent_id"),
        Index("uq_commerce_a2a_buyer_access_hash", "token_hash", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False)
    buyer_agent_id: Mapped[str] = mapped_column(String(160), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
