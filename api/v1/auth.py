"""Auth endpoints — login (email/password + Google OAuth)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from passlib.hash import bcrypt
from pydantic import BaseModel
from sqlalchemy import select

from auth.jwt import create_access_token
from core.config import settings
from core.database import async_session_factory
from core.models.user import User
from core.rbac import get_allowed_domains, get_scopes_for_role

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == body.email, User.status == "active")
        )
        user = result.scalar_one_or_none()
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not bcrypt.verify(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
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
    return LoginResponse(
        access_token=token,
        user={
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "domain": user.domain,
            "tenant_id": str(user.tenant_id),
        },
    )


class GoogleLoginRequest(BaseModel):
    credential: str  # Google ID token from Sign In With Google


@router.post("/google", response_model=LoginResponse)
async def google_login(body: GoogleLoginRequest):
    """Verify Google ID token, find-or-create user, return JWT."""
    client_id = settings.google_oauth_client_id
    if not client_id:
        raise HTTPException(status_code=501, detail="Google login not configured")

    try:
        idinfo = google_id_token.verify_oauth2_token(
            body.credential, google_requests.Request(), client_id
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Invalid Google token: {e}") from None

    email = idinfo.get("email", "")
    name = idinfo.get("name", email.split("@")[0])

    if not email:
        raise HTTPException(status_code=401, detail="Google token missing email")

    # Find or create user
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user:
            # Auto-create user with a default tenant
            from core.models.tenant import Tenant

            # Find default tenant or create one
            tenant_result = await session.execute(select(Tenant).limit(1))
            tenant = tenant_result.scalar_one_or_none()
            if not tenant:
                raise HTTPException(status_code=403, detail="No tenant configured")

            user = User(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                email=email,
                name=name,
                role="admin",
                status="active",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

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
    return LoginResponse(
        access_token=token,
        user={
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "domain": user.domain,
            "tenant_id": str(user.tenant_id),
        },
    )


@router.get("/config")
async def auth_config():
    """Return public auth configuration (Google Client ID, etc.)."""
    return {
        "google_client_id": settings.google_oauth_client_id or None,
    }
