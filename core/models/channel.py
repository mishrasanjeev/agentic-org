# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Private channel configuration, sender delegation and durable intake."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ChannelBinding(BaseModel):
    __tablename__ = "channel_bindings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("target_kind IN ('agent','workflow')"),
        CheckConstraint("reply_mode IN ('status','agent_answer')", name="ck_channel_reply_mode"),
        Index("ix_channel_bindings_owner", "tenant_id", "owner_user_id"),
        Index("ix_channel_bindings_owner_fk", "owner_user_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credentials_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    auto_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    reply_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="status", server_default="status")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class ChannelSenderLink(BaseModel):
    __tablename__ = "channel_sender_links"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "binding_id"], ["channel_bindings.tenant_id", "channel_bindings.id"]),
        UniqueConstraint("tenant_id", "binding_id", "sender_hash"),
        Index("ix_channel_links_binding", "tenant_id", "binding_id"),
        Index("ix_channel_links_user_fk", "user_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    sender_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    challenge_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class ChannelDelivery(BaseModel):
    __tablename__ = "channel_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "binding_id"], ["channel_bindings.tenant_id", "channel_bindings.id"]),
        UniqueConstraint("tenant_id", "binding_id", "event_id"),
        Index("ix_channel_deliveries_pending", "status", "created_at"),
        Index("ix_channel_deliveries_binding", "tenant_id", "binding_id", "created_at"),
        Index("ix_channel_deliveries_user_fk", "user_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    message_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    delegation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    human_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    reply_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="status", server_default="status")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="needs_review", server_default="needs_review"
    )
    result_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
