"""Auth endpoints — login (email/password + Google OAuth) + signup + logout."""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from collections import defaultdict

import bcrypt as _bcrypt
from fastapi import APIRouter, HTTPException, Request, Response
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel
from sqlalchemy import func, select

from auth.jwt import blacklist_token, create_access_token, validate_local_token
from auth.one_time_codes import consume as consume_code
from auth.one_time_codes import issue as issue_code
from core.config import settings
from core.database import async_session_factory
from core.email import send_password_reset_email, send_welcome_email
from core.models.tenant import Tenant
from core.models.user import User
from core.rbac import get_allowed_domains, get_scopes_for_role
from core.seed_tenant import seed_tenant_defaults

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


def _set_session_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    """Set the HttpOnly session cookie used by the browser SPA.

    SECURITY_AUDIT-2026-04-19 CRITICAL-01 remediation: the access token
    is still returned in the JSON body so that API clients, SDKs, CI,
    and the SSO callback can continue to function, but the browser is
    encouraged to use the cookie. Frontend migration to cookie-only
    happens in a follow-up PR which removes ``localStorage`` writes.
    """
    is_prod = os.getenv("AGENTICORG_ENV", "development").lower() == "production"
    response.set_cookie(
        key="agenticorg_session",
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    """Clear the HttpOnly session cookie on logout."""
    response.delete_cookie(key="agenticorg_session", path="/")


# Signup rate limiting now in core.auth_state (Redis-backed)


# ---------------------------------------------------------------------------
# Password policy (SOC-2 control)
# ---------------------------------------------------------------------------

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
async def signup(body: SignupRequest, request: Request, response: Response):
    """Register a new organization and admin user."""
    # Rate-limit signups per IP (Redis-backed)
    client_ip = request.client.host if request.client else "unknown"
    from core.auth_state import check_signup_rate
    if await check_signup_rate(client_ip):
        raise HTTPException(status_code=429, detail="Too many signup attempts — try again later")

    # Password policy
    _validate_password(body.password)

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
        pw_hash = _bcrypt.hashpw(body.password.encode(), _bcrypt.gensalt(rounds=12)).decode()
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
        await session.flush()

        # Seed built-in agents and prompt templates for the new org
        try:
            await seed_tenant_defaults(session, tenant.id)
        except Exception:
            logger.exception("Failed to seed defaults for tenant %s — signup continues", tenant.id)

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

    _set_session_cookie(response, token, getattr(settings, "token_ttl_minutes", 60) * 60)
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


_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_MAX = 5
_LOGIN_WINDOW = 60  # 1 minute

# Redis-backed throttling (cross-pod consistent)
_throttle_redis = None


async def _get_throttle_redis():
    """Lazy-init async Redis client for login throttling."""
    global _throttle_redis
    if _throttle_redis is not None:
        return _throttle_redis
    try:
        import redis.asyncio as aioredis

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _throttle_redis = aioredis.from_url(url, decode_responses=True)
        await _throttle_redis.ping()
        return _throttle_redis
    except Exception:
        _throttle_redis = None
        return None


async def _check_rate_limit(client_ip: str) -> bool:
    """Check and increment login rate limit. Returns True if blocked."""
    redis = await _get_throttle_redis()
    if redis:
        try:
            key = f"auth:login_attempts:{client_ip}"
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, _LOGIN_WINDOW)
            return count > _LOGIN_MAX
        except Exception:
            logger.debug("Redis throttle unavailable, using in-memory fallback")
    # In-memory fallback
    now = time.time()
    _login_attempts[client_ip] = [
        t for t in _login_attempts[client_ip] if now - t < _LOGIN_WINDOW
    ]
    if len(_login_attempts[client_ip]) >= _LOGIN_MAX:
        return True
    _login_attempts[client_ip].append(now)
    return False


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, response: Response):
    # Rate-limit login attempts per IP (Redis-backed, in-memory fallback)
    client_ip = request.client.host if request.client else "unknown"
    if await _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many login attempts — try again in 1 minute")

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

        # Auto-seed defaults for orgs created before the seed feature was added
        if tenant and user.role == "admin":
            from core.models.agent import Agent
            agent_count = await session.execute(
                select(func.count()).select_from(Agent).where(Agent.tenant_id == user.tenant_id)
            )
            if (agent_count.scalar() or 0) == 0:
                try:
                    await seed_tenant_defaults(session, user.tenant_id)
                    await session.commit()
                    logger.info("Auto-seeded defaults for pre-existing tenant %s on login", user.tenant_id)
                except Exception:
                    logger.exception("Auto-seed on login failed for tenant %s", user.tenant_id)

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
    _set_session_cookie(response, token, getattr(settings, "token_ttl_minutes", 60) * 60)
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
async def google_login(body: GoogleLoginRequest, response: Response):
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
            # Create a NEW tenant for this Google user (no cross-tenant leakage)
            org_name = f"{name}'s Organization"
            slug = _make_slug(org_name)
            slug_check = await session.execute(
                select(Tenant).where(Tenant.slug == slug)
            )
            if slug_check.scalar_one_or_none():
                slug = f"{slug}-{uuid.uuid4().hex[:6]}"

            tenant = Tenant(
                id=uuid.uuid4(),
                name=org_name,
                slug=slug,
                settings={"onboarding_complete": False, "onboarding_step": 1},
            )
            session.add(tenant)
            await session.flush()

            user = User(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                email=email,
                name=name,
                role="admin",
                domain="all",
                status="active",
            )
            session.add(user)
            await session.flush()

            # Seed built-in agents and templates for the new org
            try:
                await seed_tenant_defaults(session, tenant.id)
            except Exception:
                logger.exception("Failed to seed defaults for Google signup tenant %s", tenant.id)

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
    _set_session_cookie(response, token, getattr(settings, "token_ttl_minutes", 60) * 60)
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


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    # One of ``token`` (legacy raw JWT) or ``code`` (opaque one-time
    # code, preferred per MEDIUM-10) must be present.
    token: str | None = None
    code: str | None = None
    password: str


# Rate limiting for password reset (max 3 per email per hour)
_reset_attempts: dict[str, list[float]] = defaultdict(list)
_RESET_MAX = 3
_RESET_WINDOW = 3600  # 1 hour


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    """Send a password reset link if the email is registered."""
    email = body.email.strip().lower()

    # Rate-limit per email
    now = time.time()
    _reset_attempts[email] = [t for t in _reset_attempts[email] if now - t < _RESET_WINDOW]
    if len(_reset_attempts[email]) >= _RESET_MAX:
        # Still return success to avoid email enumeration
        return {"status": "ok", "message": "If that email is registered, a reset link has been sent."}
    _reset_attempts[email].append(now)

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == email, User.status == "active")
        )
        user = result.scalar_one_or_none()

    # Always return the same response to prevent email enumeration
    if user:
        reset_token = create_access_token(
            data={
                "sub": user.email,
                "agenticorg:tenant_id": str(user.tenant_id),
                "agenticorg:reset": True,
            },
            expires_minutes=60,
        )
        # MEDIUM-10: opaque one-time code in URL, JWT stays server-side.
        code = await issue_code("reset", reset_token, ttl_seconds=60 * 60)
        app_url = os.getenv("AGENTICORG_APP_URL", "https://app.agenticorg.ai")
        reset_link = f"{app_url}/reset-password?code={code}"
        try:
            send_password_reset_email(user.email, reset_link)
        except Exception:
            logger.exception("Password reset email failed for user")

    return {"status": "ok", "message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest):
    """Validate reset (code or legacy JWT) and set a new password."""
    token_value = body.token
    if body.code and not token_value:
        token_value = await consume_code("reset", body.code)
        if not token_value:
            raise HTTPException(status_code=400, detail="Reset link is invalid or expired")
    if not token_value:
        raise HTTPException(status_code=400, detail="Missing reset code or token")
    try:
        claims = validate_local_token(token_value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid or expired reset token: {e}") from None

    if not claims.get("agenticorg:reset"):
        raise HTTPException(status_code=400, detail="Token is not a password reset token")

    _validate_password(body.password)

    email = claims.get("sub")
    if not email:
        raise HTTPException(status_code=400, detail="Invalid reset token — missing user")

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == email, User.status == "active")
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        pw_hash = _bcrypt.hashpw(body.password.encode(), _bcrypt.gensalt(rounds=12)).decode()
        user.password_hash = pw_hash
        session.add(user)
        await session.commit()

    # Blacklist the reset token so it can't be reused
    blacklist_token(body.token)

    return {"status": "ok", "message": "Password has been reset. You can now sign in."}


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Blacklist the current token and clear the session cookie."""
    # Accept either cookie or Authorization header for logout
    # (CRITICAL-01: cookie is the new primary session carrier).
    token = request.cookies.get("agenticorg_session") or ""
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Missing session cookie or Authorization header")
    blacklist_token(token)
    _clear_session_cookie(response)
    return {"status": "logged_out"}


@router.get("/me")
async def get_current_user_profile(request: Request):
    """Return the current authenticated user's profile from the JWT.

    Used by the frontend after SSO login to hydrate the session. The
    claims are already verified by the auth middleware — we just decode
    and return them in the shape the UI expects.
    """
    # CRITICAL-01: accept the session cookie first, fall back to the
    # Authorization header for API-client compatibility.
    token = request.cookies.get("agenticorg_session") or ""
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(401, "Missing session cookie or Authorization header")
    try:
        claims = validate_local_token(token)
    except Exception as exc:
        raise HTTPException(401, "Invalid or expired token") from exc

    # Hydrate from the DB so we get the latest role/domain/name
    email = claims.get("sub", "")
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == email, User.status == "active")
        )
        user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    # Read onboarding_complete from tenant settings — must match /login and
    # /signup so routing is identical across all session hydration paths.
    # Fail closed: default to False on missing tenant or lookup failure so
    # the UI guides the user through onboarding rather than silently skipping.
    onboarding_complete = False
    try:
        async with async_session_factory() as session:
            tenant_result = await session.execute(
                select(Tenant).where(Tenant.id == user.tenant_id)
            )
            tenant = tenant_result.scalar_one_or_none()
            if tenant:
                onboarding_complete = tenant.settings.get("onboarding_complete", False)
    except Exception:
        logger.debug("Tenant lookup for onboarding_complete failed, defaulting to False")

    return {
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "domain": user.domain,
        "mfa_enabled": user.mfa_enabled,
        "onboarding_complete": onboarding_complete,
    }


@router.get("/config")
async def auth_config():
    """Return public auth configuration (Google Client ID, etc.)."""
    return {
        "google_client_id": settings.google_oauth_client_id or None,
    }
