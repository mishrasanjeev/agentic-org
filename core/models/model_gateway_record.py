# SPDX-License-Identifier: Apache-2.0
"""Routing records: one signed row per model call made while the model gateway was on.

Written by ``core.governance.model_gateway_records`` when a routed model call
ends: the correlation id that links it to the request, the use case and
agent, the routing and access policies evaluated, what was requested and what
was chosen, the model it fell back from when failover took it, the outcome,
latency, admission wait, tokens and cost. The signature covers the recorded
fields with the platform's audit key, as the audit rows are signed.

Row-level security: tenant-scoped (``v6z36_model_gateway_records``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, Float, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ModelGatewayRecord(BaseModel):
    __tablename__ = "model_gateway_records"
    __table_args__ = (
        Index("ix_model_gateway_records_tenant_created", "tenant_id", text("created_at DESC")),
        Index("ix_model_gateway_records_tenant_correlation", "tenant_id", "correlation_id"),
        Index("ix_model_gateway_records_tenant_agent", "tenant_id", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    use_case: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    access_policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    fallback_from: Mapped[str | None] = mapped_column(String(128), nullable=True)
    restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    admission_wait_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    signature: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Digests of the prompt, of what the model saw and of what it answered; the content is never stored.
    prompt_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
