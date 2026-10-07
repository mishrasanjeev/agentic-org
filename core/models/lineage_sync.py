# SPDX-License-Identifier: Apache-2.0
"""Sync sources polled on a schedule and their runs (``core/lineage/sync.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class LineageSyncSource(BaseModel):
    """A feed polled for what changed since the cursor. Row-level security: tenant-scoped, forced."""

    __tablename__ = "lineage_sync_sources"
    __table_args__ = (
        Index("ux_lineage_sync_sources_tenant_name", "tenant_id", "name", unique=True),
        Index("ix_lineage_sync_sources_tenant_due", "tenant_id", "enabled", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="feed")
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    item_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="document")
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    token: Mapped[str] = mapped_column(Text, nullable=False, default="")  # encrypted for the tenant, never returned
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    cursor: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    next_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    # A run holds the source from its claim to its finish; a lease that ran out frees it for the next claim.
    lease_owner: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    lease_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class LineageSyncRun(BaseModel):
    """One run of a source: what it received, processed, skipped and failed. Row-level security: forced."""

    __tablename__ = "lineage_sync_runs"
    __table_args__ = (
        Index("ix_lineage_sync_runs_source", "source_id"),
        Index("ix_lineage_sync_runs_tenant_started", "tenant_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage_sync_sources.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    cursor_before: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    cursor_after: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
