# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The customer agent's own records: customer sessions, mandates and prepared actions.

A bank's customer never holds a platform credential. The bank authenticates
the customer and hands the platform a signed assertion; the platform opens a
``customer_sessions`` row and issues an opaque session token whose hash is
stored here. ``customer_mandates`` hold the autonomy level and limits a
customer has set per task class (the envelope the agent executes inside).
``customer_actions`` record every action the agent prepared, executed,
refused or saw cancelled, with the explanation shown and the approval given.

Row-level security: tenant-scoped (``v6z84_customer_agent``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, CheckConstraint, Float, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class CustomerSession(BaseModel):
    __tablename__ = "customer_sessions"
    __table_args__ = (
        Index("ux_customer_sessions_token_hash", "token_hash", unique=True),
        Index("ux_customer_sessions_tenant_assertion", "tenant_id", "assertion_jti", unique=True),
        Index("ix_customer_sessions_tenant_customer", "tenant_id", "customer_ref", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    customer_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    principal_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assertion_jti: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    disclosure_acknowledged_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class CustomerMandate(BaseModel):
    __tablename__ = "customer_mandates"
    __table_args__ = (
        Index("ix_customer_mandates_tenant_customer_class", "tenant_id", "customer_ref", "task_class"),
        CheckConstraint("autonomy_level >= 0 AND autonomy_level <= 4", name="ck_customer_mandates_level"),
        CheckConstraint("period IN ('day','week','month')", name="ck_customer_mandates_period"),
        CheckConstraint("status IN ('active','revoked','expired')", name="ck_customer_mandates_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    customer_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    task_class: Mapped[str] = mapped_column(String(64), nullable=False)
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    per_action_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    period_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    period: Mapped[str] = mapped_column(String(8), nullable=False, default="month")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    counterparties: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # "window" is a reserved word in PostgreSQL; the column is time_window, the attribute keeps the API name.
    window: Mapped[dict[str, Any]] = mapped_column("time_window", JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    reconfirm_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class CustomerAction(BaseModel):
    __tablename__ = "customer_actions"
    __table_args__ = (
        Index("ix_customer_actions_tenant_customer_created", "tenant_id", "customer_ref", text("created_at DESC")),
        Index("ix_customer_actions_tenant_class_status", "tenant_id", "customer_ref", "task_class", "status"),
        CheckConstraint(
            "status IN ('prepared','executed','cancelled','refused','failed','superseded')",
            name="ck_customer_actions_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    customer_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    task_class: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="prepared")
    level_applied: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    requires: Mapped[str] = mapped_column(String(16), nullable=False, default="confirm")
    approval: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    execution: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
