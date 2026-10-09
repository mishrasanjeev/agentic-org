# SPDX-License-Identifier: Apache-2.0
"""A tenant's value for a business console setting (``core/workbench/console.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class BusinessSetting(BaseModel):
    """One setting, one tenant: the value, the one before and who changed it. Row-level security: tenant-scoped."""

    __tablename__ = "business_settings"
    __table_args__ = (Index("ux_business_settings_tenant_key", "tenant_id", "key", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    previous: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
