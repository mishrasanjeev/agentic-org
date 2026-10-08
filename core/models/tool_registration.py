# SPDX-License-Identifier: Apache-2.0
"""A tenant's tool registration: schemas and execution envelope (``core/tool_gateway/registry.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ToolRegistration(BaseModel):
    """One registered tool per tenant and name.

    Row-level security: tenant-scoped (``v6z58_tool_registrations``).
    """

    __tablename__ = "tool_registrations"
    __table_args__ = (
        CheckConstraint(
            "risk IN ('read', 'draft', 'internal-write', 'customer-write', 'money', 'destructive')",
            name="ck_tool_registrations_risk",
        ),
        CheckConstraint("timeout_seconds >= 1 AND timeout_seconds <= 300", name="ck_tool_registrations_timeout"),
        Index("ux_tool_registrations_tenant_name", "tenant_id", "name", unique=True),
        Index("ix_tool_registrations_tenant_enabled", "tenant_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    input_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk: Mapped[str] = mapped_column(String(16), nullable=False, default="read")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    max_output_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=256_000)
    untrusted_output: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
