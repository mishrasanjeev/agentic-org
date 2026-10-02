# SPDX-License-Identifier: Apache-2.0
"""Run spans: the finished spans of one agent run, stored for the console's waterfall.

Written by ``observability.timeline`` at the end of an agent run while the
run timeline is on (``AGENTICORG_TRACING_TIMELINE_ENABLED``): the run itself
and every model call, tool call and knowledge search inside it, with their
timings, outcomes and the events the governance layers attached. A row holds
identifiers, counts and outcomes; never prompts, answers, tool arguments or
retrieved text.

Row-level security: tenant-scoped (``v6z39_run_spans``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class RunSpan(BaseModel):
    __tablename__ = "run_spans"
    __table_args__ = (
        Index("ix_run_spans_tenant_trace", "tenant_id", "trace_id"),
        Index("ix_run_spans_tenant_name_started", "tenant_id", "name", text("started_at DESC")),
        Index("ix_run_spans_tenant_created", "tenant_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="internal")
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="unset")
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
