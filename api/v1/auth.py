"""Auth endpoints — login (email/password + Google OAuth) + signup."""

from __future__ import annotations

import logging
import re
import uuid

import bcrypt as _bcrypt
from fastapi import APIRouter, HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel
from sqlalchemy import select

from auth.jwt import create_access_token
from core.config import settings
from core.database import async_session_factory
from core.email import send_welcome_email
from core.models.tenant import Tenant
from core.models.user import User
from core.rbac import get_allowed_domains, get_scopes_for_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class SignupRequest(BaseModel):
    org_name: str
    admin_name: str
    admin_email: str
    password: str


def _make_slug(name: str) -> str:
    """Generate a URL-safe slug from an organization name."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-")


@router.post("/signup", response_model=LoginResponse, status_code=201)
async def signup(body: SignupRequest):
    """Register a new organization and admin user."""
    async with async_session_factory() as session:
        # Check email not already registered globally
        existing = await session.execute(
            select(User).where(User.email == body.admin_email)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

        # Generate slug and ensure uniqueness
        slug = _make_slug(body.org_name)
        slug_check = await session.execute(
            select(Tenant).where(Tenant.slug == slug)
        )
        if slug_check.scalar_one_or_none():
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"

        # Create tenant
        tenant = Tenant(
            id=uuid.uuid4(),
            name=body.org_name,
            slug=slug,
            settings={"onboarding_complete": False, "onboarding_step": 1},
        )
        session.add(tenant)
        await session.flush()

        # Create admin user
        pw_hash = _bcrypt.hashpw(body.password.encode(), _bcrypt.gensalt()).decode()
        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email=body.admin_email,
            name=body.admin_name,
            role="admin",
            domain="all",
            password_hash=pw_hash,
            status="active",
        )
        session.add(user)
        await session.commit()
        await session.refresh(tenant)
        await session.refresh(user)

    # Build JWT
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

    # Send welcome email (non-blocking)
    try:
        send_welcome_email(body.admin_email, body.org_name, body.admin_name)
    except Exception:
        logger.exception("Welcome email failed but signup succeeded")

    return LoginResponse(
        access_token=token,
        user={
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "domain": user.domain,
            "tenant_id": str(user.tenant_id),
            "org_name": tenant.name,
            "org_slug": tenant.slug,
            "onboarding_complete": tenant.settings.get("onboarding_complete", False),
        },
    )


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == body.email, User.status == "active")
        )
        user = result.scalar_one_or_none()
        if not user or not user.password_hash:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        if not _bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        # Fetch tenant for onboarding status
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.id == user.tenant_id)
        )
        tenant = tenant_result.scalar_one_or_none()
    tenant_settings = tenant.settings if tenant else {}
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
            "onboarding_complete": tenant_settings.get("onboarding_complete", False),
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
