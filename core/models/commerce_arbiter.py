# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Arbiter decisions: one append-only row per ``core.commerce.arbiter.decide`` call.

Row-level security: tenant-scoped (``v6z88_commerce_ledger``). The table is
append-only (trigger ``commerce_arbiter_decisions_guard``) except two one-time
transitions: an ``allow`` row is consumed once (``consumed_at`` filled), and
the Grantex countersignature reference is attached once
(``grantex_grant_jti`` and ``grantex_recorded_at`` filled).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class CommerceArbiterDecisionRow(BaseModel):
    __tablename__ = "commerce_arbiter_decisions"
    __table_args__ = (
        Index("ix_commerce_arbiter_decisions_tenant_decided", "tenant_id", text("decided_at DESC")),
        Index("ix_commerce_arbiter_decisions_scope", "tenant_id", "merchant_id", "seller_agent_id"),
        Index("ix_commerce_arbiter_decisions_payload", "tenant_id", "payload_hash"),
    )

    decision_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False)
    buyer_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    rules_evaluated: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    failing_rule: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_hashes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # Reserved for the Grantex decision+jwt countersignature (CONTRACT section 7).
    grantex_grant_jti: Mapped[str | None] = mapped_column(String(255), nullable=True)
    grantex_recorded_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
