"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session, get_tenant_session
from core.models.user import User


@dataclass(frozen=True, slots=True)
class ActiveHumanAdmin:
    """Authoritative local identity for high-risk control-plane mutations."""

    user_id: UUID
    tenant_id: UUID
    email: str
    role: str


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


def get_current_tenant(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", None)
    if not tid:
        raise HTTPException(401, "No tenant context")
    return tid


def get_current_user(request: Request) -> dict:
    claims = getattr(request.state, "claims", None)
    if not claims:
        raise HTTPException(401, "Not authenticated")
    return claims


def require_scope(scope: str):
    def checker(request: Request):
        scopes = getattr(request.state, "scopes", [])
        if scope not in scopes and not any(s.startswith("agenticorg:admin") for s in scopes):
            raise HTTPException(403, f"Missing scope: {scope}")

    return Depends(checker)


# Shared dependency: every tenant control-plane route MUST use this.
# It verifies the caller has admin privileges before any mutation.
require_tenant_admin = require_scope("agenticorg:admin")


async def get_active_human_admin(request: Request) -> ActiveHumanAdmin:
    """Require an active same-tenant DB admin authenticated with a human session.

    Scope claims are only a preliminary route guard.  Readiness mutations use
    this dependency so API keys, delegated agents, stale roles, and disabled or
    cross-tenant users cannot become governance actors.
    """

    claims = get_current_user(request)
    auth_mode = getattr(request.state, "auth_mode", None)
    subject = str(claims.get("sub") or "")
    if auth_mode in {"api_key", "grantex"} or subject.startswith("apikey:") or claims.get("grantex:grant_id"):
        raise HTTPException(403, "A human tenant administrator is required")
    if auth_mode not in {None, "legacy"}:
        raise HTTPException(403, "Unsupported authentication mode for readiness governance")

    try:
        tenant_id = UUID(get_current_tenant(request))
    except (TypeError, ValueError) as exc:
        raise HTTPException(401, "Invalid tenant context") from exc

    user_id: UUID | None = None
    raw_user_id = claims.get("agenticorg:user_id")
    if raw_user_id:
        try:
            user_id = UUID(str(raw_user_id))
        except (TypeError, ValueError) as exc:
            raise HTTPException(403, "Authenticated user identity is invalid") from exc
    email = str(claims.get("email") or subject).strip().lower()
    if user_id is None and not email:
        raise HTTPException(403, "Authenticated user identity is required")

    identity = User.id == user_id if user_id is not None else func.lower(User.email) == email
    async with get_tenant_session(tenant_id, None) as session:
        result = await session.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                identity,
                User.status == "active",
                User.role == "admin",
            )
        )
        user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(403, "An active same-tenant administrator is required")
    return ActiveHumanAdmin(user_id=user.id, tenant_id=user.tenant_id, email=user.email, role=user.role)


def get_user_domains(request: Request) -> list[str] | None:
    claims = getattr(request.state, "claims", {})
    return claims.get("agenticorg:domains")


def get_user_role(request: Request) -> str:
    claims = getattr(request.state, "claims", {})
    return claims.get("role", "")
