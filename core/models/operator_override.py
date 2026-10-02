# SPDX-License-Identifier: Apache-2.0
"""Operator override rows: a halt or throttle an administrator placed on a target.

A row names one target (a model provider, a model, one agent, every agent, a
workflow definition, a connector, one tool or the whole tool pipeline) and the
mode applied to it. Active rows (``released_at`` NULL and not expired) are read
by ``core.governance.operator_override`` at every enforcement point.

Row-level security: tenant-scoped (``v6z32_operator_overrides``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

TARGET_KINDS: tuple[str, ...] = (
    "provider",
    "model",
    "agent",
    "all_agents",
    "workflow",
    "connector",
    "tool",
    "tool_pipeline",
)
MODES: tuple[str, ...] = ("halt", "throttle")


class OperatorOverride(BaseModel):
    __tablename__ = "operator_overrides"
    __table_args__ = (
        CheckConstraint(
            "target_kind IN ('provider','model','agent','all_agents','workflow','connector','tool','tool_pipeline')",
            name="ck_operator_overrides_target_kind",
        ),
        CheckConstraint("mode IN ('halt','throttle')", name="ck_operator_overrides_mode"),
        CheckConstraint(
            "(mode = 'halt' AND limit_per_minute IS NULL) OR (mode = 'throttle' AND limit_per_minute >= 0)",
            name="ck_operator_overrides_limit",
        ),
        Index("ix_operator_overrides_tenant_active", "tenant_id", "released_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Empty for the kinds that name no single target (all_agents, tool_pipeline).
    target_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
