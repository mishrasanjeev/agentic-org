"""Admin API for tenant-scoped BYO AI provider credentials.

Closes S0-09 (PR-1). Admin-gated router on top of
``core.models.tenant_ai_credential.TenantAICredential`` +
``core.ai_providers.resolver``. Never returns raw tokens — only prefix/
suffix/status/timestamps/metadata.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from core.ai_providers.resolver import (
    ProviderNotConfigured,
    invalidate_cache,
    mask_token,
)
from core.database import get_tenant_session
from core.models.tenant_ai_credential import (
    CREDENTIAL_KIND_ALLOWLIST,
    PROVIDER_ALLOWLIST,
    STATUS_ALLOWLIST,
    TenantAICredential,
)

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/tenant-ai-credentials",
    tags=["Tenant AI Credentials"],
    dependencies=[require_tenant_admin],
)


# ── Schemas ──────────────────────────────────────────────────────────


class TenantAICredentialCreate(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    credential_kind: str = Field(..., min_length=1, max_length=32)
    api_key: str = Field(..., min_length=1, max_length=4096)
    label: str | None = Field(None, max_length=255)
    provider_config: dict[str, Any] | None = None

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in PROVIDER_ALLOWLIST:
            raise ValueError(
                f"provider must be one of: {sorted(PROVIDER_ALLOWLIST)}"
            )
        return v

    @field_validator("credential_kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in CREDENTIAL_KIND_ALLOWLIST:
            raise ValueError(
                f"credential_kind must be one of: {sorted(CREDENTIAL_KIND_ALLOWLIST)}"
            )
        return v

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("api_key is required")
        if len(v) < 8:
            raise ValueError("api_key looks too short to be valid")
        return v


class TenantAICredentialUpdate(BaseModel):
    api_key: str | None = Field(None, min_length=1, max_length=4096)
    label: str | None = Field(None, max_length=255)
    provider_config: dict[str, Any] | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if v not in STATUS_ALLOWLIST:
            raise ValueError(
                f"status must be one of: {sorted(STATUS_ALLOWLIST)}"
            )
        return v


class TenantAICredentialOut(BaseModel):
    id: str
    provider: str
    credential_kind: str
    status: str
    label: str | None = None
    display_prefix: str | None = None
    display_suffix: str | None = None
    provider_config: dict[str, Any] | None = None
    last_health_check_at: str | None = None
    last_health_check_error: str | None = None
    last_used_at: str | None = None
    rotated_at: str | None = None
    created_at: str
    updated_at: str


def _to_out(row: TenantAICredential) -> TenantAICredentialOut:
    """Serialize for the API. Never touches credentials_encrypted."""
    return TenantAICredentialOut(
        id=str(row.id),
        provider=row.provider,
        credential_kind=row.credential_kind,
        status=row.status,
        label=row.label,
        display_prefix=row.display_prefix,
        display_suffix=row.display_suffix,
        provider_config=row.provider_config,
        last_health_check_at=(
            row.last_health_check_at.isoformat()
            if row.last_health_check_at else None
        ),
        last_health_check_error=row.last_health_check_error,
        last_used_at=(
            row.last_used_at.isoformat() if row.last_used_at else None
        ),
        rotated_at=(
            row.rotated_at.isoformat() if row.rotated_at else None
        ),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("", response_model=list[TenantAICredentialOut])
async def list_credentials(
    tenant_id: str = Depends(get_current_tenant),
) -> list[TenantAICredentialOut]:
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(TenantAICredential)
            .where(TenantAICredential.tenant_id == tid)
            .order_by(TenantAICredential.created_at.desc())
        )
        rows = result.scalars().all()
    return [_to_out(r) for r in rows]


@router.post(
    "",
    response_model=TenantAICredentialOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_credential(
    body: TenantAICredentialCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> TenantAICredentialOut:
    from core.crypto.tenant_secrets import encrypt_for_tenant

    tid = _uuid.UUID(tenant_id)
    prefix, suffix = mask_token(body.api_key)
    ciphertext = await encrypt_for_tenant(body.api_key, tid)

    row = TenantAICredential(
        id=_uuid.uuid4(),
        tenant_id=tid,
        provider=body.provider,
        credential_kind=body.credential_kind,
        credentials_encrypted={"_encrypted": ciphertext},
        status="unverified",
        display_prefix=prefix,
        display_suffix=suffix,
        label=body.label,
        provider_config=body.provider_config,
    )
    async with get_tenant_session(tid) as session:
        session.add(row)
        try:
            await session.flush()
        except Exception as exc:
            logger.warning("tenant_ai_credential_create_failed", error=str(exc))
            raise HTTPException(
                409,
                f"Credential for provider={body.provider!r} "
                f"kind={body.credential_kind!r} already exists for this tenant. "
                "Use PATCH to rotate.",
            ) from exc

    invalidate_cache(tid, body.provider, body.credential_kind)
    _audit(
        "tenant_ai_credential.created",
        tid,
        row.id,
        {"provider": body.provider, "credential_kind": body.credential_kind},
    )
    return _to_out(row)


@router.get("/{credential_id}", response_model=TenantAICredentialOut)
async def get_credential(
    credential_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> TenantAICredentialOut:
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(credential_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid credential_id") from exc

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(TenantAICredential).where(
                TenantAICredential.id == cid,
                TenantAICredential.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Credential not found")
    return _to_out(row)


@router.patch("/{credential_id}", response_model=TenantAICredentialOut)
async def update_credential(
    credential_id: str,
    body: TenantAICredentialUpdate,
    tenant_id: str = Depends(get_current_tenant),
) -> TenantAICredentialOut:
    """Rotate the token (``api_key`` set) or update non-secret
    metadata. Setting ``api_key`` stamps ``rotated_at``.
    """
    from core.crypto.tenant_secrets import encrypt_for_tenant

    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(credential_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid credential_id") from exc

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(TenantAICredential).where(
                TenantAICredential.id == cid,
                TenantAICredential.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Credential not found")

        rotated = False
        if body.api_key is not None:
            ciphertext = await encrypt_for_tenant(body.api_key, tid)
            row.credentials_encrypted = {"_encrypted": ciphertext}
            prefix, suffix = mask_token(body.api_key)
            row.display_prefix = prefix
            row.display_suffix = suffix
            row.rotated_at = datetime.now(UTC)
            row.status = "unverified"
            rotated = True
        if body.label is not None:
            row.label = body.label
        if body.provider_config is not None:
            row.provider_config = body.provider_config
        if body.status is not None:
            row.status = body.status

        await session.flush()

    invalidate_cache(tid, row.provider, row.credential_kind)
    _audit(
        "tenant_ai_credential.rotated" if rotated else "tenant_ai_credential.updated",
        tid,
        row.id,
        {"provider": row.provider, "credential_kind": row.credential_kind},
    )
    return _to_out(row)


@router.delete(
    "/{credential_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_credential(
    credential_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(credential_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid credential_id") from exc

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(TenantAICredential).where(
                TenantAICredential.id == cid,
                TenantAICredential.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Credential not found")
        provider = row.provider
        kind = row.credential_kind
        await session.delete(row)

    invalidate_cache(tid, provider, kind)
    _audit(
        "tenant_ai_credential.deleted",
        tid,
        cid,
        {"provider": provider, "credential_kind": kind},
    )


@router.post("/{credential_id}/test")
async def test_credential(
    credential_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Live-probe the credential against the provider.

    Calls each provider's cheapest identity endpoint so we know the
    token actually works without running a real completion.
    """
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(credential_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid credential_id") from exc

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(TenantAICredential).where(
                TenantAICredential.id == cid,
                TenantAICredential.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Credential not found")

    from core.ai_providers.health import probe_provider

    try:
        probe_result = await probe_provider(tid, row.provider, row.credential_kind)
        status_value = "active" if probe_result["ok"] else "failing"
        err = None if probe_result["ok"] else probe_result.get("error", "")
    except ProviderNotConfigured as exc:
        # Shouldn't happen — we just loaded the row — but surface honestly
        status_value = "failing"
        err = str(exc)[:500]
    except Exception as exc:
        logger.exception("tenant_ai_credential_probe_failed")
        status_value = "failing"
        # CodeQL py/stack-trace-exposure (alert #69, 2026-05-02): the
        # exception class alone is enough signal for the operator to
        # correlate with server logs. ``str(exc)`` may include
        # request context (URLs, headers, payload fragments) that
        # shouldn't surface in API responses. The full traceback is
        # captured by ``logger.exception`` above.
        err = type(exc).__name__

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(TenantAICredential).where(TenantAICredential.id == cid)
        )
        db_row = result.scalar_one_or_none()
        if db_row is not None:
            db_row.status = status_value
            db_row.last_health_check_at = datetime.now(UTC)
            db_row.last_health_check_error = err
            await session.flush()

    invalidate_cache(tid, row.provider, row.credential_kind)
    _audit(
        "tenant_ai_credential.tested",
        tid,
        cid,
        {
            "provider": row.provider,
            "credential_kind": row.credential_kind,
            "result": status_value,
        },
    )
    return {
        "tested": True,
        "status": status_value,
        "error": err,
        "credential_id": str(cid),
    }


# ── Audit log helper ─────────────────────────────────────────────────


def _audit(
    action: str,
    tenant_id: _uuid.UUID,
    target_id: _uuid.UUID,
    details: dict[str, Any],
) -> None:
    """Emit a structured audit event. Never includes the secret.

    The audit log table itself is owned by ``core.models.audit.AuditLog``
    and written via a best-effort structlog bridge — same pattern used
    by other admin routes. If the write fails we don't fail the caller.
    """
    try:
        logger.info(
            "audit_event",
            action=action,
            tenant_id=str(tenant_id),
            target_id=str(target_id),
            **details,
        )
    except Exception as exc:
        logger.debug("audit_emit_failed", error=str(exc))
