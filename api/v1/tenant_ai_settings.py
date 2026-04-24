"""Admin API for per-tenant AI configuration.

Closes S0-08 (PR-2). GET/PUT the tenant's effective LLM + embedding
config. Validates against ``core.ai_providers.catalog`` so operators
can't flip an env var into a model/dimension mismatch.

Admin-gated at the router level.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from core.ai_providers.catalog import (
    EMBEDDING_CATALOG,
    LLM_CATALOG,
    embedding_models_for,
    embedding_providers,
    find_embedding,
    find_llm,
    llm_models_for,
    llm_providers,
)
from core.ai_providers.settings import invalidate_tenant_ai_setting_cache
from core.database import get_tenant_session
from core.models.tenant_ai_setting import (
    FALLBACK_POLICIES,
    ROUTING_POLICIES,
    TenantAISetting,
)

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/tenant-ai-settings",
    tags=["Tenant AI Settings"],
    dependencies=[require_tenant_admin],
)


# ── Schemas ──────────────────────────────────────────────────────────


class TenantAISettingOut(BaseModel):
    tenant_id: str
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_fallback_model: str | None = None
    llm_routing_policy: str = "auto"
    max_input_tokens: int | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    ai_fallback_policy: str = "allow"
    updated_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TenantAISettingUpdate(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_fallback_model: str | None = None
    llm_routing_policy: str | None = None
    max_input_tokens: int | None = Field(None, ge=1, le=2_000_000)
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = Field(None, ge=1, le=8192)
    chunk_size: int | None = Field(None, ge=32, le=8192)
    chunk_overlap: int | None = Field(None, ge=0, le=1024)
    ai_fallback_policy: str | None = None

    @field_validator("llm_routing_policy")
    @classmethod
    def _validate_routing(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if v not in ROUTING_POLICIES:
            raise ValueError(
                f"llm_routing_policy must be one of: {sorted(ROUTING_POLICIES)}"
            )
        return v

    @field_validator("ai_fallback_policy")
    @classmethod
    def _validate_fallback(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if v not in FALLBACK_POLICIES:
            raise ValueError(
                f"ai_fallback_policy must be one of: {sorted(FALLBACK_POLICIES)}"
            )
        return v


class RegistryOut(BaseModel):
    llm: dict[str, Any]
    embedding: dict[str, Any]


def _to_out(row: TenantAISetting | None, tenant_id: str) -> TenantAISettingOut:
    if row is None:
        return TenantAISettingOut(tenant_id=tenant_id)
    return TenantAISettingOut(
        tenant_id=str(row.tenant_id),
        llm_provider=row.llm_provider,
        llm_model=row.llm_model,
        llm_fallback_model=row.llm_fallback_model,
        llm_routing_policy=row.llm_routing_policy,
        max_input_tokens=row.max_input_tokens,
        embedding_provider=row.embedding_provider,
        embedding_model=row.embedding_model,
        embedding_dimensions=row.embedding_dimensions,
        chunk_size=row.chunk_size,
        chunk_overlap=row.chunk_overlap,
        ai_fallback_policy=row.ai_fallback_policy,
        updated_by=str(row.updated_by) if row.updated_by else None,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/registry", response_model=RegistryOut)
async def list_registry() -> RegistryOut:
    """Expose the catalog so the UI can render valid provider/model
    pickers without having to ship the allowlist to the client.
    """
    llm_items = {
        provider: [
            {
                "model": entry.model,
                "context_window": entry.context_window,
                "max_output_tokens": entry.max_output_tokens,
                "supports_tools": entry.supports_tools,
                "supports_vision": entry.supports_vision,
                "notes": entry.notes,
            }
            for entry in LLM_CATALOG
            if entry.provider == provider
        ]
        for provider in llm_providers()
    }
    embedding_items = {
        provider: [
            {
                "model": entry.model,
                "dimensions": entry.dimensions,
                "max_input_tokens": entry.max_input_tokens,
                "notes": entry.notes,
            }
            for entry in EMBEDDING_CATALOG
            if entry.provider == provider
        ]
        for provider in embedding_providers()
    }
    return RegistryOut(llm=llm_items, embedding=embedding_items)


@router.get("", response_model=TenantAISettingOut)
async def get_setting(
    tenant_id: str = Depends(get_current_tenant),
) -> TenantAISettingOut:
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(TenantAISetting).where(TenantAISetting.tenant_id == tid)
        )
        row = result.scalar_one_or_none()
    return _to_out(row, tenant_id)


@router.put("", response_model=TenantAISettingOut)
async def put_setting(
    body: TenantAISettingUpdate,
    tenant_id: str = Depends(get_current_tenant),
) -> TenantAISettingOut:
    """Upsert the tenant's AI setting. Validates every (provider, model)
    against the catalog and embedding_dimensions against the pinned
    dimension for the selected embedding model.
    """
    tid = _uuid.UUID(tenant_id)

    # ─── Catalog validation ─────────────────────────────────────────
    if body.llm_provider is not None or body.llm_model is not None:
        # Both or neither; a half-specified LLM choice is invalid.
        if not (body.llm_provider and body.llm_model):
            raise HTTPException(
                422,
                "llm_provider and llm_model must both be set (or both unset).",
            )
        if body.llm_provider not in llm_providers():
            raise HTTPException(
                422,
                f"Unknown llm_provider {body.llm_provider!r}. "
                f"Valid: {list(llm_providers())}.",
            )
        if find_llm(body.llm_provider, body.llm_model) is None:
            raise HTTPException(
                422,
                f"Unknown llm_model {body.llm_model!r} for provider "
                f"{body.llm_provider!r}. Valid: "
                f"{list(llm_models_for(body.llm_provider))}.",
            )
    if body.llm_fallback_model is not None and body.llm_provider is not None:
        if find_llm(body.llm_provider, body.llm_fallback_model) is None:
            raise HTTPException(
                422,
                f"Unknown llm_fallback_model {body.llm_fallback_model!r} for "
                f"provider {body.llm_provider!r}.",
            )
    if body.embedding_provider is not None or body.embedding_model is not None:
        if not (body.embedding_provider and body.embedding_model):
            raise HTTPException(
                422,
                "embedding_provider and embedding_model must both be set "
                "(or both unset).",
            )
        if body.embedding_provider not in embedding_providers():
            raise HTTPException(
                422,
                f"Unknown embedding_provider {body.embedding_provider!r}. "
                f"Valid: {list(embedding_providers())}.",
            )
        catalog_entry = find_embedding(
            body.embedding_provider, body.embedding_model
        )
        if catalog_entry is None:
            raise HTTPException(
                422,
                f"Unknown embedding_model {body.embedding_model!r} for "
                f"provider {body.embedding_provider!r}. Valid: "
                f"{list(embedding_models_for(body.embedding_provider))}.",
            )
        if (
            body.embedding_dimensions is not None
            and body.embedding_dimensions != catalog_entry.dimensions
        ):
            raise HTTPException(
                422,
                f"embedding_dimensions {body.embedding_dimensions} does not "
                f"match catalog dimension {catalog_entry.dimensions} for "
                f"{body.embedding_provider}/{body.embedding_model}. Fix the "
                "client to match the catalog — changing dimensions requires "
                "a backfill (see scripts/embedding_rotate.py).",
            )

    # ─── Upsert ─────────────────────────────────────────────────────
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(TenantAISetting).where(TenantAISetting.tenant_id == tid)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = TenantAISetting(tenant_id=tid)
            session.add(row)

        # Apply every provided field
        for field in (
            "llm_provider",
            "llm_model",
            "llm_fallback_model",
            "llm_routing_policy",
            "max_input_tokens",
            "embedding_provider",
            "embedding_model",
            "embedding_dimensions",
            "chunk_size",
            "chunk_overlap",
            "ai_fallback_policy",
        ):
            incoming = getattr(body, field)
            if incoming is not None:
                setattr(row, field, incoming)

        # Default embedding_dimensions from catalog if the admin didn't
        # pass one but did pass a provider+model.
        if (
            row.embedding_provider
            and row.embedding_model
            and not row.embedding_dimensions
        ):
            catalog_entry = find_embedding(row.embedding_provider, row.embedding_model)
            if catalog_entry is not None:
                row.embedding_dimensions = catalog_entry.dimensions

        row.updated_at = datetime.now(UTC)
        await session.flush()

    invalidate_tenant_ai_setting_cache(tid)
    _audit(
        "tenant_ai_setting.updated",
        tid,
        body.model_dump(exclude_none=True),
    )

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(TenantAISetting).where(TenantAISetting.tenant_id == tid)
        )
        row = result.scalar_one()
    return _to_out(row, tenant_id)


def _audit(
    action: str,
    tenant_id: _uuid.UUID,
    diff: dict[str, Any],
) -> None:
    try:
        logger.info(
            "audit_event",
            action=action,
            tenant_id=str(tenant_id),
            diff=diff,
        )
    except Exception as exc:
        logger.debug("audit_emit_failed", error=str(exc))
