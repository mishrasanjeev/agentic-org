"""A2A Task ORM model."""

from __future__ import annotations

import uuid

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel, TenantMixin, TimestampMixin


class A2ATask(BaseModel, TenantMixin, TimestampMixin):
    __tablename__ = "a2a_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    agent_type: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    input_data: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    output_data: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
