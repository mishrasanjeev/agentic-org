# SPDX-License-Identifier: Apache-2.0
"""A detector finding under human disposition (``core/txn/findings.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class TxnFinding(BaseModel):
    """One finding, kept once under its fingerprint. Row-level security: tenant-scoped, forced."""

    __tablename__ = "txn_findings"
    __table_args__ = (
        Index("ux_txn_findings_tenant_fingerprint", "tenant_id", "fingerprint", unique=True),
        Index("ix_txn_findings_tenant_status", "tenant_id", "status"),
        Index("ix_txn_findings_tenant_entity", "tenant_id", "entity_ref"),
        Index("ix_txn_findings_tenant_detected", "tenant_id", "detected_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="account")
    entity_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    record_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    disposition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    case_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
