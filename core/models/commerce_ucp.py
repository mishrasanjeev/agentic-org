# SPDX-License-Identifier: Apache-2.0
"""UCP transaction state: checkout sessions, carts, orders, order webhooks and idempotency records.

Row-level security: every table is tenant-scoped with ENABLE + FORCE RLS
(``v6z89_commerce_sessions_orders``). ``state`` and ``snapshot`` hold the
resource in its version-neutral internal shape (``core.commerce.ucp``);
payment credentials are never stored (only the instrument's id, handler,
type, display and a SHA-256 of the credential).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, BigInteger, Boolean, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class CommerceCheckoutSessionRow(BaseModel):
    __tablename__ = "commerce_checkout_sessions"
    __table_args__ = (
        Index("ix_commerce_checkout_sessions_scope", "tenant_id", "merchant_id", text("created_at DESC")),
        Index("ix_commerce_checkout_sessions_status", "tenant_id", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False)
    serving_host: Mapped[str] = mapped_column(String(255), nullable=False)
    ucp_version: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    platform_profile_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    cart_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decision_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class CommerceCartRow(BaseModel):
    __tablename__ = "commerce_carts"
    __table_args__ = (Index("ix_commerce_carts_scope", "tenant_id", "merchant_id", text("created_at DESC")),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False)
    serving_host: Mapped[str] = mapped_column(String(255), nullable=False)
    ucp_version: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    platform_profile_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checkout_session_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class CommerceOrderRow(BaseModel):
    __tablename__ = "commerce_orders"
    __table_args__ = (Index("ix_commerce_orders_scope", "tenant_id", "merchant_id", text("created_at DESC")),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False)
    checkout_session_id: Mapped[str] = mapped_column(String(80), nullable=False)
    serving_host: Mapped[str] = mapped_column(String(255), nullable=False)
    ucp_version: Mapped[str] = mapped_column(String(10), nullable=False)
    platform_profile_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    decision_id: Mapped[str] = mapped_column(String(80), nullable=False)
    executor: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_order_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class CommerceOrderWebhookSubscriptionRow(BaseModel):
    __tablename__ = "commerce_order_webhook_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    scheme: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    platform_profile_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    signing_keys_encrypted: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    active_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class CommerceOrderWebhookDeliveryRow(BaseModel):
    __tablename__ = "commerce_order_webhook_deliveries"
    __table_args__ = (
        Index("ix_commerce_order_webhook_deliveries_due", "tenant_id", "status", "next_attempt_at"),
        Index("ix_commerce_order_webhook_deliveries_order", "tenant_id", "order_id"),
        Index("ix_commerce_order_webhook_deliveries_subscription", "subscription_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce_order_webhook_subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    order_id: Mapped[str] = mapped_column(String(80), nullable=False)
    order_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    webhook_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class CommerceUcpIdempotencyRow(BaseModel):
    __tablename__ = "commerce_ucp_idempotency"
    __table_args__ = (Index("ix_commerce_ucp_idempotency_expiry", "tenant_id", "expires_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
