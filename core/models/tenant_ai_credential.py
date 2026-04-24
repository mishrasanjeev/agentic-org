"""Tenant-scoped encrypted AI provider credentials.

Part of S0-09 closure (PR-1 of docs/STRICT_REPO_S0_CLOSURE_PLAN_2026-04-24.md).

Each row stores one (provider, credential_kind) pair for a tenant with the raw
token enveloped via ``core.crypto.encrypt_for_tenant``. The API surface never
returns the raw token — only a ``display_prefix`` + ``display_suffix`` pair so
operators can identify which key they rotated without ever exposing it.

Consumed by ``core/ai_providers/resolver.py`` before the platform-env fallback
in ``core/langgraph/llm_factory.py`` + ``core/llm/router.py`` +
``core/embeddings.py`` + ``api/v1/voice.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

# Provider allowlist — keep in sync with core/ai_providers/catalog.py.
# Extend via Alembic data migration + allowlist update; NEVER accept
# "free-form provider name" from the API.
PROVIDER_ALLOWLIST = frozenset({
    "gemini",
    "openai",
    "anthropic",
    "azure_openai",
    "openai_compatible",
    "voyage",
    "cohere",
    "ragflow",
    "stt_deepgram",
    "stt_azure",
    "tts_elevenlabs",
    "tts_azure",
})

CREDENTIAL_KIND_ALLOWLIST = frozenset({
    "llm",
    "embedding",
    "rag",
    "stt",
    "tts",
})

STATUS_ALLOWLIST = frozenset({
    "active",  # in use, last health check passed
    "inactive",  # admin-disabled, resolver skips
    "unverified",  # created but /test endpoint never run
    "failing",  # last /test failed — resolver falls back per policy
})


class TenantAICredential(BaseModel):
    __tablename__ = "tenant_ai_credentials"
    __table_args__ = (
        # One BYO token per (provider, kind) per tenant. Admins rotate
        # in place via PATCH; delete + recreate is also allowed.
        UniqueConstraint(
            "tenant_id",
            "provider",
            "credential_kind",
            name="uq_tenant_ai_cred",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # JSONB wrapper so we can add additional enveloped values in the
    # future (e.g. Azure resource endpoint + key). Today the only key
    # inside is ``_encrypted`` holding the envelope ciphertext.
    credentials_encrypted: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unverified"
    )
    # Masked identifier — first 4 chars of the raw token. Populated on
    # create/rotate. Never stores anything that could be used to forge
    # the token. Used by the UI to help operators identify which key
    # they rotated without exposing the body.
    display_prefix: Mapped[str | None] = mapped_column(String(8), nullable=True)
    display_suffix: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Free-form label, admin-provided (e.g. "Finance team OpenAI", "Prod").
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Optional JSON blob for provider-specific non-secret config
    # (e.g. Azure endpoint URL, OpenAI-compatible base_url).
    provider_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    last_health_check_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_health_check_error: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    rotated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
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
