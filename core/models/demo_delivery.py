# SPDX-License-Identifier: Apache-2.0
"""Tenant-owned encrypted marketing delivery history."""
import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class DemoDeliveryJob(BaseModel):
    __tablename__ = "demo_delivery_jobs"
    __table_args__ = (
        Index("ix_demo_delivery_tenant_created", "tenant_id", "created_at"),
        CheckConstraint("notification_status IN ('pending','processing','smtp_accepted','failed','unknown')",
                        name="demo_notification_state"),
        CheckConstraint("confirmation_status IN ('pending','processing','smtp_accepted','failed','unknown')",
                        name="demo_confirmation_state"),
        CheckConstraint(
            "agent_status IN ('pending','processing','completed','needs_review','failed','skipped','unknown')",
            name="demo_agent_state"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    notification_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    confirmation_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    agent_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    error_codes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    publish_after: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
