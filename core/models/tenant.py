"""Tenant ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class Tenant(BaseModel):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="enterprise")
    data_region: Mapped[str] = mapped_column(String(10), nullable=False, default="IN")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # v4.7.0: BYOK/CMEK — customer-owned KMS key resource name. When set,
    # all envelope-encrypted payloads for this tenant use this KEK instead
    # of the platform default.  Empty string = platform-managed.
    byok_kek_resource: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    users = relationship("User", back_populates="tenant")
    agents = relationship("Agent", back_populates="tenant")
