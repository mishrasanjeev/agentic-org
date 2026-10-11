# SPDX-License-Identifier: Apache-2.0
"""Customer sessions: the bank's signed assertion in, an opaque session token out.

The customer never holds a platform credential. The bank authenticates the
customer in its own channel and signs a short-lived assertion (a JWT with
``iss``, ``aud``, ``sub`` the bank's pseudonymous customer reference, ``iat``
and ``exp``) with a key published at the configured JWKS URL. The platform
verifies it, opens a session and returns a token prefixed ``cas_`` whose
SHA-256 hash is stored; the token itself is never persisted. Sessions expire,
can be revoked, and record when the customer acknowledged the AI disclosure,
without which no turn runs.

A strict runtime refuses to open sessions until the assertion issuer and JWKS
URL are configured; a relaxed runtime does the same, so there is no path that
trusts an unsigned customer identity.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

TOKEN_PREFIX = "cas_"
ALGORITHMS: tuple[str, ...] = ("RS256", "ES256")
CHANNELS: tuple[str, ...] = ("web", "app", "whatsapp", "voice", "api")
MAX_CUSTOMER_REF = 128


class CustomerAssertionError(ValueError):
    """The bank's assertion could not be verified."""


class CustomerAgentDisabledError(RuntimeError):
    """The customer agent is off for this deployment."""


def enabled() -> bool:
    return bool(settings.customer_agent_enabled)


def tenant_id() -> uuid.UUID | None:
    raw = str(settings.customer_agent_tenant_id or "").strip()
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


def company_id() -> str:
    return str(settings.customer_agent_company_id or "").strip()


def agent_id() -> str:
    return str(settings.customer_agent_agent_id or "").strip()


def disclosure_text() -> str:
    return str(settings.customer_agent_disclosure or "").strip() or (
        "You are talking to the bank's AI assistant. It can explain, suggest and prepare actions for you; "
        "it never asks for your PIN, password or OTP, and you can ask for a person at any time."
    )


@dataclass(frozen=True)
class CustomerIdentity:
    customer_ref: str
    channel: str
    claims: dict[str, Any] = field(default_factory=dict)


def _signing_key(token: str) -> Any:
    """The bank's public key for ``token`` from the configured JWKS (replaced by tests)."""
    import jwt

    client = jwt.PyJWKClient(settings.customer_agent_assertion_jwks_uri, cache_keys=True)
    return client.get_signing_key_from_jwt(token).key


def verify_assertion(assertion: str, *, channel: str = "web") -> CustomerIdentity:
    """The customer the bank vouches for, or ``CustomerAssertionError``."""
    import jwt

    issuer = str(settings.customer_agent_assertion_issuer or "").strip()
    jwks_uri = str(settings.customer_agent_assertion_jwks_uri or "").strip()
    audience = str(settings.customer_agent_assertion_audience or "").strip()
    if not issuer or not jwks_uri or not audience:
        raise CustomerAssertionError("customer assertions are not configured (issuer, JWKS URL and audience)")
    if not isinstance(assertion, str) or assertion.count(".") != 2:
        raise CustomerAssertionError("malformed assertion")
    try:
        header = jwt.get_unverified_header(assertion)
    except jwt.PyJWTError as exc:
        raise CustomerAssertionError(f"malformed assertion header: {exc}") from exc
    if header.get("alg") not in ALGORITHMS:
        raise CustomerAssertionError("unsupported assertion algorithm")
    try:
        key = _signing_key(assertion)
        claims = jwt.decode(
            assertion,
            key,
            algorithms=list(ALGORITHMS),
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iat", "sub"]},
            leeway=30,
        )
    except jwt.PyJWTError as exc:
        raise CustomerAssertionError(f"assertion verification failed: {exc}") from exc
    # enterprise-gate: broad-except-ok reason=jwks-outage-fails-closed-and-logged
    except Exception as exc:
        logger.warning("customer_assertion_key_unavailable", error_type=type(exc).__name__)
        raise CustomerAssertionError("the bank's signing keys could not be read") from exc
    ref = str(claims.get("sub") or "").strip()
    if not ref:
        raise CustomerAssertionError("assertion names no customer")
    chosen = str(claims.get("channel") or channel or "web").lower()
    if chosen not in CHANNELS:
        chosen = "web"
    return CustomerIdentity(customer_ref=ref[:MAX_CUSTOMER_REF], channel=chosen, claims=dict(claims))


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_session_token(token: str) -> bool:
    return isinstance(token, str) and token.startswith(TOKEN_PREFIX) and len(token) > len(TOKEN_PREFIX) + 20


def session_view(row: Any, *, token: str | None = None) -> dict[str, Any]:
    view: dict[str, Any] = {
        "session_id": str(row.id),
        "customer_ref": row.customer_ref,
        "channel": row.channel,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "disclosure": disclosure_text(),
        "disclosure_acknowledged": row.disclosure_acknowledged_at is not None,
        "principal_id": row.principal_id,
        "autonomy_ceiling": int(settings.customer_agent_max_autonomy),
    }
    if token:
        view["session_token"] = token
    return view


async def open_session(assertion: str, *, channel: str = "web") -> tuple[Any, str]:
    """Verify the bank's assertion and open a session; the row and the one-time token."""
    if not enabled():
        raise CustomerAgentDisabledError("customer agent is off")
    tid = tenant_id()
    if tid is None:
        raise CustomerAssertionError("the customer agent has no tenant configured")
    identity = await asyncio.to_thread(verify_assertion, assertion, channel=channel)
    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerSession

    token = new_token()
    now = datetime.now(UTC)
    ttl = max(1, int(settings.customer_agent_session_ttl_minutes))
    company = company_id()
    row = CustomerSession(
        id=uuid.uuid4(),
        tenant_id=tid,
        company_id=uuid.UUID(company) if company else None,
        customer_ref=identity.customer_ref,
        principal_id=f"customer:{identity.customer_ref}"[:MAX_CUSTOMER_REF],
        channel=identity.channel,
        token_hash=token_hash(token),
        issued_at=now,
        expires_at=now + timedelta(minutes=ttl),
    )
    async with get_tenant_session(tid) as session:
        session.add(row)
    logger.info("customer_session_opened", channel=identity.channel, session_id=str(row.id))
    return row, token


async def authenticate(token: str) -> Any | None:
    """The live session for a token, or None (unknown, expired, revoked)."""
    if not enabled() or not is_session_token(token):
        return None
    tid = tenant_id()
    if tid is None:
        return None
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerSession

    digest = token_hash(token)
    now = datetime.now(UTC)
    async with get_tenant_session(tid) as session:
        row = (
            await session.execute(
                select(CustomerSession).where(CustomerSession.tenant_id == tid, CustomerSession.token_hash == digest)
            )
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            return None
        row.last_seen_at = now
    return row


async def acknowledge_disclosure(row: Any) -> Any:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerSession

    async with get_tenant_session(row.tenant_id) as session:
        stored = (
            await session.execute(select(CustomerSession).where(CustomerSession.id == row.id))
        ).scalar_one_or_none()
        if stored is None:
            return row
        if stored.disclosure_acknowledged_at is None:
            stored.disclosure_acknowledged_at = datetime.now(UTC)
        row.disclosure_acknowledged_at = stored.disclosure_acknowledged_at
    return row


async def revoke_session(row: Any) -> None:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerSession

    async with get_tenant_session(row.tenant_id) as session:
        stored = (
            await session.execute(select(CustomerSession).where(CustomerSession.id == row.id))
        ).scalar_one_or_none()
        if stored is not None and stored.revoked_at is None:
            stored.revoked_at = datetime.now(UTC)
    logger.info("customer_session_revoked", session_id=str(row.id))
