"""ToolCall ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Index, Integer, SmallInteger, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ToolCall(BaseModel):
    """One tool call. The tool gateway writes a row per call (core/insights/runtime_counters.py, v6z115).

    A gateway call has no workflow step and its agent may not be a stored agent, so ``step_exec_id`` and
    ``agent_id`` are nullable; ``connector_name`` is how the gateway addresses the connector. Forced RLS.
    """

    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_tenant_called", "tenant_id", "called_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step_exec_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    connector_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    connector_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), default=1)
    replayed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # FinOps attribution of the run the call was made in (core/finops/attribution.py); nullable.
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True)
    application: Mapped[str | None] = mapped_column(String(64), nullable=True)
    called_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
