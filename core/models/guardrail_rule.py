# SPDX-License-Identifier: Apache-2.0
"""Guardrail rules: which detector runs at which stage of a call, and what happens when it finds something.

One row per rule a tenant administrator writes. ``core.governance.guardrails``
evaluates the enabled rows matching a stage (and the agent, use case or risk
tier a row names) in priority order; ``flag`` records, ``mask``, ``redact``
and ``tokenise`` transform, ``block`` refuses, all behind the
``guardrails.enforce`` flag.

Row-level security: tenant-scoped (``v6z38_guardrail_rules``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class GuardrailRule(BaseModel):
    __tablename__ = "guardrail_rules"
    __table_args__ = (
        CheckConstraint("stage IN ('input','retrieval','output','action')", name="ck_guardrail_rules_stage"),
        CheckConstraint("action IN ('flag','mask','redact','tokenise','block')", name="ck_guardrail_rules_action"),
        CheckConstraint("threshold >= 0 AND threshold <= 1", name="ck_guardrail_rules_threshold"),
        CheckConstraint("priority >= 0", name="ck_guardrail_rules_priority"),
        Index("ix_guardrail_rules_tenant_enabled", "tenant_id", "enabled", "stage", "priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    detector: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="flag")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True)
