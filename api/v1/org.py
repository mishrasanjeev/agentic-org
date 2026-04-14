"""Organization management endpoints for profile, members, invites, and onboarding."""

from __future__ import annotations

import logging
import os
import re
import uuid

import bcrypt as _bcrypt
from fastapi import APIRouter, HTTPException, Request
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import select, update

from api.deps import require_tenant_admin
from auth.jwt import create_access_token, validate_local_token
from core.config import settings
from core.database import async_session_factory
from core.email import send_invite_email
from core.models.tenant import Tenant
from core.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/org", tags=["Organization"])


def _get_tenant_id(request: Request) -> str:
    """Extract tenant_id from authenticated request state."""
    tid = getattr(request.state, "tenant_id", None)
    if not tid:
        raise HTTPException(status_code=401, detail="Missing tenant context")
    return tid


def _validate_password(password: str) -> None:
    """Raise HTTPException(400) if password does not meet policy."""
    if (
        len(password) < 8
        or not re.search(r"[A-Z]", password)
        or not re.search(r"[a-z]", password)
        or not re.search(r"[0-9]", password)
    ):
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters with uppercase, lowercase, and a number",
        )


def _decode_invite_claims(token: str) -> dict:
    """Validate an invite token and return its claims."""
    try:
        claims = validate_local_token(token)
    except (ValueError, JWTError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or expired invite token: {e}",
        ) from None

    if not claims.get("agenticorg:invite"):
        raise HTTPException(status_code=400, detail="Token is not an invite token")
    if not claims.get("agenticorg:user_id"):
        raise HTTPException(status_code=400, detail="Invalid invite token - missing user ID")
    return claims


class InviteRequest(BaseModel):
    email: str
    name: str = ""
    role: str = "analyst"
    domain: str | None = None


class AcceptInviteRequest(BaseModel):
    token: str
    name: str | None = None
    password: str


class OnboardingUpdate(BaseModel):
    onboarding_step: int | None = None
    onboarding_complete: bool | None = None


@router.get("/profile", dependencies=[require_tenant_admin])
async def get_profile(request: Request):
    """Return tenant info and settings for the current user's organization."""
    tenant_id = _get_tenant_id(request)
    async with async_session_factory() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
        )
        tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "slug": tenant.slug,
        "plan": tenant.plan,
        "data_region": tenant.data_region,
        "settings": tenant.settings,
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
    }


@router.get("/members", dependencies=[require_tenant_admin])
async def list_members(request: Request):
    """List all users in the current tenant."""
    tenant_id = _get_tenant_id(request)
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(
                User.tenant_id == uuid.UUID(tenant_id),
                User.status != "deleted",
            )
        )
        users = result.scalars().all()
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "name": u.name,
            "role": u.role,
            "domain": u.domain,
            "status": u.status,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/invite", status_code=201, dependencies=[require_tenant_admin])
async def invite_member(body: InviteRequest, request: Request):
    """Create a pending user and send an invite email with a JWT token."""
    allowed_invite_roles = {"admin", "domain_lead", "analyst", "auditor", "developer"}
    if body.role not in allowed_invite_roles:
        raise HTTPException(
            400,
            f"Invalid role: {body.role}. Allowed: {', '.join(sorted(allowed_invite_roles))}",
        )

    tenant_id = _get_tenant_id(request)
    inviter_email = getattr(request.state, "user_sub", "unknown")

    async with async_session_factory() as session:
        existing = await session.execute(
            select(User).where(
                User.tenant_id == uuid.UUID(tenant_id),
                User.email == body.email,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="User already exists in organization")

        tenant_result = await session.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
        )
        tenant = tenant_result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Organization not found")

        user = User(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            email=body.email,
            name=body.name or body.email.split("@")[0],
            role=body.role,
            domain=body.domain,
            status="pending",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    invite_token = create_access_token(
        data={
            "sub": body.email,
            "agenticorg:tenant_id": tenant_id,
            "agenticorg:invite": True,
            "agenticorg:user_id": str(user.id),
        },
        expires_minutes=1440,
    )

    app_url = os.getenv("AGENTICORG_APP_URL", "https://app.agenticorg.ai")
    invite_link = f"{app_url}/accept-invite?token={invite_token}"

    try:
        send_invite_email(body.email, tenant.name, inviter_email, body.role, invite_link)
    except Exception:
        logger.exception("Invite email failed but user was created")

    return {
        "status": "invited",
        "user_id": str(user.id),
        "email": body.email,
        "invite_link": invite_link,
    }


@router.get("/invite-info")
async def get_invite_info(token: str):
    """Return invite metadata for the accept-invite screen."""
    claims = _decode_invite_claims(token)
    user_id = claims["agenticorg:user_id"]

    async with async_session_factory() as session:
        result = await session.execute(
            select(User, Tenant)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(User.id == uuid.UUID(user_id))
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Invited user not found")

        user, tenant = row
        return {
            "org_name": tenant.name,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "status": user.status,
        }


@router.post("/accept-invite")
async def accept_invite(body: AcceptInviteRequest):
    """Validate invite JWT, set password, and activate the user."""
    claims = _decode_invite_claims(body.token)
    user_id = claims["agenticorg:user_id"]
    _validate_password(body.password)

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Invited user not found")
        if user.status == "active":
            raise HTTPException(status_code=409, detail="Invitation already accepted")
        invite_email = claims.get("sub")
        if invite_email and invite_email != user.email:
            raise HTTPException(status_code=400, detail="Invite token does not match invited user")

        user.password_hash = _bcrypt.hashpw(
            body.password.encode(),
            _bcrypt.gensalt(rounds=12),
        ).decode()
        user.status = "active"
        if body.name and body.name.strip():
            user.name = body.name.strip()
        session.add(user)
        await session.commit()
        await session.refresh(user)

    from core.rbac import get_allowed_domains, get_scopes_for_role

    token = create_access_token(
        data={
            "sub": user.email,
            "agenticorg:tenant_id": str(user.tenant_id),
            "grantex:scopes": get_scopes_for_role(user.role),
            "name": user.name,
            "role": user.role,
            "domain": user.domain,
            "agenticorg:domains": get_allowed_domains(user.role),
        },
        expires_minutes=getattr(settings, "token_ttl_minutes", 60),
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "domain": user.domain,
            "tenant_id": str(user.tenant_id),
        },
    }


@router.put("/onboarding", dependencies=[require_tenant_admin])
async def update_onboarding(body: OnboardingUpdate, request: Request):
    """Update onboarding step or completion flag in tenant settings."""
    tenant_id = _get_tenant_id(request)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Organization not found")

        updated_settings = dict(tenant.settings) if tenant.settings else {}
        if body.onboarding_step is not None:
            updated_settings["onboarding_step"] = body.onboarding_step
        if body.onboarding_complete is not None:
            updated_settings["onboarding_complete"] = body.onboarding_complete

        await session.execute(
            update(Tenant)
            .where(Tenant.id == uuid.UUID(tenant_id))
            .values(settings=updated_settings)
        )
        await session.commit()

    return {"status": "updated", "settings": updated_settings}


@router.delete("/members/{user_id}", dependencies=[require_tenant_admin])
async def deactivate_member(user_id: str, request: Request):
    """Soft-deactivate a member by setting status to inactive."""
    tenant_id = _get_tenant_id(request)
    current_user_email = getattr(request.state, "user_sub", "")

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(
                User.id == uuid.UUID(user_id),
                User.tenant_id == uuid.UUID(tenant_id),
            )
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Member not found")
        if user.email == current_user_email:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

        user.status = "inactive"
        session.add(user)
        await session.commit()

    return {"status": "deactivated", "user_id": user_id}
