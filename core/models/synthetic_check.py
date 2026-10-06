# SPDX-License-Identifier: Apache-2.0
"""Synthetic checks: a tenant's scheduled probes and their results.

A check (``observability.synthetic``) names a probe kind, its configuration
and how often it runs; every run leaves a result row with the status, the
latency and the reasons. A result holds counts and reasons, never a prompt's
answer or retrieved text.

Row-level security: tenant-scoped (``v6z42_synthetic_checks``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class SyntheticCheck(BaseModel):
    __tablename__ = "synthetic_checks"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('model','knowledge','audit_chain','guardrail','adversarial','eval_dataset')",
            name="ck_synthetic_checks_kind",
        ),
        CheckConstraint("interval_minutes >= 5 AND interval_minutes <= 1440", name="ck_synthetic_checks_interval"),
        Index("ux_synthetic_checks_tenant_name", "tenant_id", "name", unique=True),
        Index("ix_synthetic_checks_tenant_enabled", "tenant_id", "enabled", "last_run_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True)


class SyntheticCheckResult(BaseModel):
    __tablename__ = "synthetic_check_results"
    __table_args__ = (
        CheckConstraint("status IN ('ok','failed','error')", name="ck_synthetic_check_results_status"),
        CheckConstraint("latency_ms >= 0", name="ck_synthetic_check_results_latency"),
        # Leads with the foreign key: the results of one check, newest first, and the cascade on its delete.
        Index("ix_synthetic_check_results_check_started", "check_id", text("started_at DESC")),
        Index("ix_synthetic_check_results_tenant_started", "tenant_id", text("started_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("synthetic_checks.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="schedule")
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
