"""API key management endpoints — generate, list, revoke keys for SDK/CLI/MCP access."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone

import bcrypt as _bcrypt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update

from core.database import async_session_factory
from core.models.api_key import APIKey

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/org/api-keys", tags=["API Keys"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", None)
    if not tid:
        raise HTTPException(status_code=401, detail="Missing tenant context")
    return tid


def _get_user_sub(request: Request) -> str:
    return getattr(request.state, "user_sub", "")


def _generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key. Returns (full_key, prefix, hash)."""
    # Format: ao_sk_{40 random hex chars}
    raw = secrets.token_hex(20)
    full_key = f"ao_sk_{raw}"
    prefix = f"ao_sk_{raw[:6]}"
    key_hash = _bcrypt.hashpw(full_key.encode(), _bcrypt.gensalt(rounds=10)).decode()
    return full_key, prefix, key_hash


def verify_api_key(plain_key: str, key_hash: str) -> bool:
    """Verify a plain API key against its bcrypt hash."""
    return _bcrypt.checkpw(plain_key.encode(), key_hash.encode())


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateKeyRequest(BaseModel):
    name: str
    scopes: list[str] = []
    expires_days: int | None = None  # None = never expires


class CreateKeyResponse(BaseModel):
    id: str
    name: str
    prefix: str
    key: str  # Only returned once at creation
    scopes: list[str]
    expires_at: str | None
    created_at: str


# ---------------------------------------------------------------------------
# POST /org/api-keys — Generate new API key
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_api_key(body: CreateKeyRequest, request: Request):
    """Generate a new API key. The full key is only shown once."""
    tenant_id = _get_tenant_id(request)
    user_sub = _get_user_sub(request)

    # Look up user by email
    from core.models.user import User
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(
                User.tenant_id == uuid.UUID(tenant_id),
                User.email == user_sub,
            )
        )
        user = result.scalar_one_or_none()
        user_id = user.id if user else uuid.UUID(tenant_id)

    # Limit: max 10 active keys per tenant
    async with async_session_factory() as session:
        count_result = await session.execute(
            select(APIKey).where(
                APIKey.tenant_id == uuid.UUID(tenant_id),
                APIKey.status == "active",
            )
        )
        active_keys = count_result.scalars().all()
        if len(active_keys) >= 10:
            raise HTTPException(status_code=400, detail="Maximum 10 active API keys per organization")

    full_key, prefix, key_hash = _generate_api_key()

    expires_at = None
    if body.expires_days:
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)

    default_scopes = [
        "agents:read", "agents:run", "connectors:read",
        "mcp:read", "mcp:call", "a2a:read",
    ]

    api_key = APIKey(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        user_id=user_id,
        name=body.name,
        prefix=prefix,
        key_hash=key_hash,
        scopes=body.scopes or default_scopes,
        expires_at=expires_at,
        status="active",
    )

    async with async_session_factory() as session:
        session.add(api_key)
        await session.commit()
        await session.refresh(api_key)

    return {
        "id": str(api_key.id),
        "name": api_key.name,
        "prefix": api_key.prefix,
        "key": full_key,
        "scopes": api_key.scopes,
        "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
        "created_at": api_key.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /org/api-keys — List all keys (no secrets)
# ---------------------------------------------------------------------------

@router.get("")
async def list_api_keys(request: Request):
    """List all API keys for the current tenant (secrets are never returned)."""
    tenant_id = _get_tenant_id(request)

    async with async_session_factory() as session:
        result = await session.execute(
            select(APIKey)
            .where(APIKey.tenant_id == uuid.UUID(tenant_id))
            .order_by(APIKey.created_at.desc())
        )
        keys = result.scalars().all()

    return [
        {
            "id": str(k.id),
            "name": k.name,
            "prefix": k.prefix,
            "scopes": k.scopes,
            "status": k.status,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "created_at": k.created_at.isoformat(),
        }
        for k in keys
    ]


# ---------------------------------------------------------------------------
# DELETE /org/api-keys/{key_id} — Revoke a key
# ---------------------------------------------------------------------------

@router.delete("/{key_id}")
async def revoke_api_key(key_id: str, request: Request):
    """Revoke an API key. This is permanent."""
    tenant_id = _get_tenant_id(request)

    async with async_session_factory() as session:
        result = await session.execute(
            select(APIKey).where(
                APIKey.id == uuid.UUID(key_id),
                APIKey.tenant_id == uuid.UUID(tenant_id),
            )
        )
        key = result.scalar_one_or_none()
        if not key:
            raise HTTPException(status_code=404, detail="API key not found")
        if key.status == "revoked":
            raise HTTPException(status_code=400, detail="Key already revoked")

        await session.execute(
            update(APIKey)
            .where(APIKey.id == uuid.UUID(key_id))
            .values(status="revoked")
        )
        await session.commit()

    return {"status": "revoked", "key_id": key_id}
