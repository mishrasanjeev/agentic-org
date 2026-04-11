"""Tenant branding — white-label / custom domain support.

Each tenant can override the app's visual identity and metadata so
resellers can embed AgenticOrg under their own brand.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class TenantBranding(BaseModel):
    __tablename__ = "tenant_branding"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_branding_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Product name displayed in the UI
    product_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default="AgenticOrg"
    )

    # Logo URL (PNG or SVG)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    favicon_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Hex colors (with #)
    primary_color: Mapped[str] = mapped_column(
        String(7), nullable=False, default="#7c3aed"
    )
    accent_color: Mapped[str] = mapped_column(
        String(7), nullable=False, default="#1e293b"
    )

    # Custom domain (e.g., app.customer.com). Nginx must route it.
    custom_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Footer text, support email, etc.
    support_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    footer_text: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
