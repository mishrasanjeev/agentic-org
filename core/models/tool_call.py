"""ToolCall ORM model."""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import TIMESTAMP, ForeignKey, Integer, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from core.models.base import BaseModel

class ToolCall(BaseModel):
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step_exec_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    connector_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    input_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    http_status: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    llm_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    called_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
