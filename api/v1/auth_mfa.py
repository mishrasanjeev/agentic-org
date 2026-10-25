"""Authenticator-app (TOTP) multi-factor sign-in.

Enrolment, for any signed-in user:

1. ``POST /auth/mfa/setup`` returns a QR code (and the key, for typing) for a
   fresh secret held as *pending*.
2. ``POST /auth/mfa/enable`` takes a code from the app; a correct code turns
   MFA on and returns ten one-time recovery codes, shown once.

Sign-in: when MFA is on, ``/auth/login`` and ``/auth/google`` answer with an
``mfa_challenge`` instead of a session, and ``POST /auth/mfa/verify`` exchanges
that challenge plus an authenticator (or recovery) code for the session.

Turning MFA off or replacing recovery codes needs a current code. An admin
who has lost access asks another admin to reset it (``DELETE
/org/members/{id}/mfa``).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.client_ip import client_ip as resolve_client_ip
from api.route_metadata import route_meta
from api.v1.auth import (
    LoginResponse,
    _check_rate_limit,
    _clear_rate_limit,
    mfa_enrolled,
    start_session,
)
from auth import totp
from auth.one_time_codes import MFA_CHALLENGE_TTL_SECONDS, record_attempt
from auth.one_time_codes import consume as consume_code
from auth.one_time_codes import peek as peek_code
from core.crypto.credential_vault import decrypt_credential, encrypt_credential
from core.database import get_tenant_session
from core.models.tenant import Tenant
from core.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/mfa", tags=["Auth"])

CHALLENGE_MAX_ATTEMPTS = 5
PENDING_SECRET_TTL = timedelta(minutes=15)


class MfaCodeRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=32)


class MfaVerifyRequest(BaseModel):
    challenge: str = Field(..., min_length=16, max_length=128)
    code: str = Field(..., min_length=6, max_length=32)


def _session_identity(request: Request) -> tuple[uuid.UUID, uuid.UUID | None, str]:
    """Tenant, user id and e-mail of a browser session (API keys cannot manage MFA)."""
    if getattr(request.state, "auth_mode", "") != "legacy":
        raise HTTPException(400, "Two-factor settings belong to a signed-in user, not an API key")
    claims = getattr(request.state, "claims", None)
    tenant_value = getattr(request.state, "tenant_id", "")
    if not isinstance(claims, dict) or not tenant_value:
        raise HTTPException(401, "Missing authenticated session context")
    try:
        tenant_id = uuid.UUID(str(tenant_value))
        user_value = claims.get("agenticorg:user_id")
        user_id = uuid.UUID(str(user_value)) if user_value else None
    except (TypeError, ValueError):
        raise HTTPException(401, "Invalid authenticated session context") from None
    email = claims.get("sub", "")
    if not user_id and not email:
        raise HTTPException(401, "Missing authenticated user context")
    return tenant_id, user_id, email


async def _locked_user(session, tenant_id: uuid.UUID, user_id: uuid.UUID | None, email: str) -> User:
    filters = [User.tenant_id == tenant_id, User.status == "active"]
    filters.append(User.id == user_id if user_id else User.email == email)
    result = await session.execute(select(User).where(*filters).with_for_update())
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(401, "User not found")
    return user


def check_second_factor(user: User, code: str) -> str | None:
    """Accept an authenticator code or an unused recovery code; mutate ``user`` to record it.

    Returns ``"totp"`` or ``"recovery"``, or ``None`` when the code is wrong,
    already used, or replayed. Callers hold the user row ``FOR UPDATE``.
    """
    if not user.mfa_totp_secret:
        return None
    secret = decrypt_credential(user.mfa_totp_secret)
    step = totp.verify(secret, code, last_used_step=user.mfa_last_totp_step)
    if step is not None:
        user.mfa_last_totp_step = step
        return "totp"
    if totp.looks_like_recovery_code(code):
        digest = totp.hash_recovery_code(str(user.id), code)
        remaining = list(user.mfa_recovery_codes or [])
        if digest in remaining:
            remaining.remove(digest)
            user.mfa_recovery_codes = remaining
            return "recovery"
    return None


def _issue_recovery_codes(user: User) -> list[str]:
    codes = totp.generate_recovery_codes()
    user.mfa_recovery_codes = [totp.hash_recovery_code(str(user.id), code) for code in codes]
    return codes


def clear_mfa(user: User) -> None:
    user.mfa_enabled = False
    user.mfa_totp_secret = None
    user.mfa_pending_secret = None
    user.mfa_pending_created_at = None
    user.mfa_last_totp_step = None
    user.mfa_recovery_codes = []
    user.mfa_enabled_at = None


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="auth.mfa.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="auth.mfa.status",
)
async def mfa_status(request: Request):
    tenant_id, user_id, email = _session_identity(request)
    async with get_tenant_session(tenant_id) as session:
        filters = [User.tenant_id == tenant_id, User.status == "active"]
        filters.append(User.id == user_id if user_id else User.email == email)
        user = (await session.execute(select(User).where(*filters))).scalar_one_or_none()
        if user is None:
            raise HTTPException(401, "User not found")
    enrolled = mfa_enrolled(user)
    return {
        "enabled": enrolled,
        "enabled_at": user.mfa_enabled_at.isoformat() if enrolled and user.mfa_enabled_at else None,
        "recovery_codes_remaining": len(user.mfa_recovery_codes or []) if enrolled else 0,
    }


@router.post("/setup")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="auth.mfa.manage",
    rate_limit="auth-mutating",
    idempotency="replaces-pending-secret",
    audit_event="auth.mfa.setup",
)
async def mfa_setup(request: Request):
    """Start enrolment: a fresh secret as a QR code and a typed key, pending until confirmed."""
    tenant_id, user_id, email = _session_identity(request)
    async with get_tenant_session(tenant_id) as session:
        user = await _locked_user(session, tenant_id, user_id, email)
        if mfa_enrolled(user):
            raise HTTPException(409, "Two-factor sign-in is already on. Turn it off first to use a new device.")
        secret = totp.generate_secret()
        user.mfa_pending_secret = encrypt_credential(secret)
        user.mfa_pending_created_at = datetime.now(UTC)
        session.add(user)
        await session.commit()
        account = user.email
    uri = totp.provisioning_uri(secret, account)
    return {
        "qr_code": totp.qr_svg_data_uri(uri),
        "otpauth_uri": uri,
        "secret": totp.format_secret(secret),
        "issuer": totp.ISSUER,
        "account": account,
        "expires_in": int(PENDING_SECRET_TTL.total_seconds()),
    }


@router.post("/enable")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="auth.mfa.manage",
    rate_limit="auth-mutating",
    idempotency="enrolment-confirmed-once",
    audit_event="auth.mfa.enable",
)
async def mfa_enable(body: MfaCodeRequest, request: Request):
    """Confirm the pending secret with a code from the app; returns recovery codes once."""
    tenant_id, user_id, email = _session_identity(request)
    async with get_tenant_session(tenant_id) as session:
        user = await _locked_user(session, tenant_id, user_id, email)
        if mfa_enrolled(user):
            raise HTTPException(409, "Two-factor sign-in is already on")
        created = user.mfa_pending_created_at
        if not user.mfa_pending_secret or created is None or datetime.now(UTC) - created > PENDING_SECRET_TTL:
            raise HTTPException(400, "The QR code has expired. Start setup again.")
        secret = decrypt_credential(user.mfa_pending_secret)
        step = totp.verify(secret, body.code)
        if step is None:
            raise HTTPException(400, "That code is not right. Check the time on your phone and try the newest code.")
        user.mfa_totp_secret = user.mfa_pending_secret
        user.mfa_pending_secret = None
        user.mfa_pending_created_at = None
        user.mfa_last_totp_step = step
        user.mfa_enabled = True
        user.mfa_enabled_at = datetime.now(UTC)
        codes = _issue_recovery_codes(user)
        session.add(user)
        await session.commit()
    return {"enabled": True, "recovery_codes": codes}


@router.post("/disable")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="auth.mfa.manage",
    rate_limit="auth-mutating",
    idempotency="clears-enrolment",
    audit_event="auth.mfa.disable",
)
async def mfa_disable(body: MfaCodeRequest, request: Request):
    tenant_id, user_id, email = _session_identity(request)
    async with get_tenant_session(tenant_id) as session:
        user = await _locked_user(session, tenant_id, user_id, email)
        if not mfa_enrolled(user):
            raise HTTPException(409, "Two-factor sign-in is not on")
        if check_second_factor(user, body.code) is None:
            raise HTTPException(400, "That code is not right")
        clear_mfa(user)
        session.add(user)
        await session.commit()
    return {"enabled": False}


@router.post("/recovery-codes")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="auth.mfa.manage",
    rate_limit="auth-mutating",
    idempotency="replaces-recovery-codes",
    audit_event="auth.mfa.recovery_codes",
)
async def mfa_regenerate_recovery_codes(body: MfaCodeRequest, request: Request):
    """Replace every recovery code (the old ones stop working); needs a current code."""
    tenant_id, user_id, email = _session_identity(request)
    async with get_tenant_session(tenant_id) as session:
        user = await _locked_user(session, tenant_id, user_id, email)
        if not mfa_enrolled(user):
            raise HTTPException(409, "Two-factor sign-in is not on")
        if check_second_factor(user, body.code) is None:
            raise HTTPException(400, "That code is not right")
        codes = _issue_recovery_codes(user)
        session.add(user)
        await session.commit()
    return {"recovery_codes": codes}


@router.post("/verify", response_model=LoginResponse)
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:auth.mfa.verify",
    rate_limit="auth-login",
    idempotency="one-time-challenge-consumed-on-success",
    audit_event="auth.mfa.verify",
    public_reason="second-factor-of-a-pending-sign-in",
)
async def mfa_verify(body: MfaVerifyRequest, request: Request, response: Response):
    """Finish a sign-in: the challenge from ``/auth/login`` or ``/auth/google`` plus a code."""
    client_ip = resolve_client_ip(request)
    if await _check_rate_limit(client_ip):
        raise HTTPException(429, "Too many attempts — try again in 1 minute")

    expired = "This sign-in has expired. Sign in again."
    payload = await peek_code("mfa", body.challenge)
    if not payload:
        raise HTTPException(401, expired)
    if await record_attempt("mfa", body.challenge, MFA_CHALLENGE_TTL_SECONDS) > CHALLENGE_MAX_ATTEMPTS:
        await consume_code("mfa", body.challenge)
        raise HTTPException(401, "Too many wrong codes. Sign in again.")
    try:
        claims = json.loads(payload)
        tenant_id = uuid.UUID(claims["tenant_id"])
        user_id = uuid.UUID(claims["user_id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(401, expired) from None

    async with get_tenant_session(tenant_id) as session:
        user = await _locked_user(session, tenant_id, user_id, "")
        if not mfa_enrolled(user):
            raise HTTPException(401, expired)
        method = check_second_factor(user, body.code)
        if method is None:
            raise HTTPException(401, "That code is not right")
        # Spend the challenge before committing, so two racing requests with
        # valid codes cannot both become sessions.
        if await consume_code("mfa", body.challenge) is None:
            raise HTTPException(401, expired)
        session.add(user)
        await session.commit()
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
        recovery_left = len(user.mfa_recovery_codes or [])

    await _clear_rate_limit(client_ip)
    if method == "recovery":
        logger.info("MFA sign-in used a recovery code (%s left)", recovery_left)
    return start_session(response, user, tenant.settings if tenant else {})
