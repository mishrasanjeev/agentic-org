"""Per-tenant governance configuration.

Stores the compliance / data-region / retention controls that the Settings
page exposes. Before PR-B these were UI-only React state and the admin's
choices evaporated on reload — see docs/mcp-product-model.md and the
Enterprise Readiness Plan Phase 4.

One row per tenant. Every write goes through the governance API which
emits an audit event (actor, old value, new value, tenant) in the same
transaction.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class GovernanceConfig(BaseModel):
    """One row per tenant — the persisted Settings page controls."""

    __tablename__ = "governance_config"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # PII masking in logs/traces/audit. Default true — production requires it.
    pii_masking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Data-region residency. Enum-ish string (IN | EU | US) — validated at API layer.
    data_region: Mapped[str] = mapped_column(String(8), nullable=False, default="IN")

    # Audit log retention in years. Bounded [1, 10].
    audit_retention_years: Mapped[int] = mapped_column(Integer, nullable=False, default=7)

    # Last-writer stamps.
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
