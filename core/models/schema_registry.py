"""Schema registry ORM model."""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from core.models.base import BaseModel

class SchemaRegistry(BaseModel):
    __tablename__ = "schema_registry"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    json_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
