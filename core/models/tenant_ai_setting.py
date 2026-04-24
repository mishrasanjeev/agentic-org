"""Tenant AI configuration — per-tenant LLM + embedding model choice.

Closes S0-08 (PR-2). One row per tenant; admins pick provider, model,
and dimensions (validated against ``core.ai_providers.catalog``). The
resolver + llm_factory + embeddings consult this row before the
platform env defaults.

Related: ``core.models.tenant_ai_credential`` holds the BYO tokens
this setting references; without a matching credential the tenant
falls back per ``ai_fallback_policy``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

ROUTING_POLICIES = frozenset({"auto", "single", "fallback_only", "disabled"})
FALLBACK_POLICIES = frozenset({"allow", "deny"})


class TenantAISetting(BaseModel):
    __tablename__ = "tenant_ai_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # LLM selection
    llm_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    llm_fallback_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    llm_routing_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="auto"
    )
    max_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Embedding selection
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_overlap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # BYO policy — when "deny", the resolver refuses platform-env fallback
    # for this tenant (enterprise regulated mode).
    ai_fallback_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default="allow"
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
