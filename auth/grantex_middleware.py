"""Grantex-aware FastAPI auth middleware.

Supports triple-mode authentication:
  1. API keys (``ao_sk_...``) verified against the ``api_keys`` table
  2. Grantex RS256 grant tokens (agents, external callers via A2A/MCP)
  3. Legacy HS256 JWTs (existing users, backward-compatible)

A bearer is treated as a Grantex grant token only when its unverified
header is RS256 AND its unverified ``iss`` matches the configured Grantex
issuer. Any other RS256 token goes through the legacy validator (which
supports the ``AGENTICORG_JWT_PUBLIC_KEY_URL`` JWKS path), so an attacker
cannot steer arbitrary tokens into the Grantex path and force a JWKS fetch.

Grantex configuration (env, ``AGENTICORG_`` prefix — ``core/config.py`` is
outside this module's change set, so the values are read here):

  AGENTICORG_GRANTEX_AUDIENCE  expected ``aud`` for this deployment. Required
                               in strict runtimes; grant tokens are rejected
                               before any network call when it is unset.
  AGENTICORG_GRANTEX_ISSUER    expected ``iss``. Defaults to the issuer the
                               Grantex SDK derives from ``GRANTEX_BASE_URL``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.client_ip import client_ip as resolve_client_ip
from auth.jwt import extract_scopes, extract_tenant_id, validate_token
from core.auth_state import (
    clear_auth_failures,
    get_user_session_state,
    is_ip_blocked,
    record_auth_failure,
)
from core.config import is_strict_runtime_env, settings

logger = structlog.get_logger()

_JWKS_TTL_SECONDS = 300.0
_JWKS_NEGATIVE_TTL_SECONDS = 60.0
_JWKS_FETCH_TIMEOUT_SECONDS = 5.0


class GrantexAuthError(ValueError):
    """Grant token could not be verified or bound to a tenant."""


def _b64url_json(segment: str) -> dict[str, Any]:
    padding = 4 - len(segment) % 4
    if padding != 4:
        segment += "=" * padding
    decoded = json.loads(base64.urlsafe_b64decode(segment))
    return decoded if isinstance(decoded, dict) else {}


def _unverified_parts(token: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return (header, payload) without verification, or None if malformed."""
    try:
        header_b64, payload_b64, _sig = token.split(".")
        return _b64url_json(header_b64), _b64url_json(payload_b64)
    # enterprise-gate: broad-except-ok reason=malformed-jwt-segments-fall-through-to-fail-closed-validation
    except Exception:
        return None


def grantex_jwks_uri() -> str:
    base_url = os.getenv("GRANTEX_BASE_URL", "https://api.grantex.dev").rstrip("/")
    return f"{base_url}/.well-known/jwks.json"


def grantex_expected_issuer() -> str:
    configured = (
        str(getattr(settings, "grantex_issuer", "") or "").strip().rstrip("/")
        or os.getenv("AGENTICORG_GRANTEX_ISSUER", "").strip().rstrip("/")
    )
    if configured:
        return configured
    try:
        from grantex._verify import _derive_issuer_from_jwks_uri

        return _derive_issuer_from_jwks_uri(grantex_jwks_uri()).rstrip("/")
    # enterprise-gate: broad-except-ok reason=missing-grantex-sdk-does-not-enable-grantex-mode-empty-issuer-fails-closed
    except Exception:
        return ""


def grantex_expected_audience() -> str:
    return (
        str(getattr(settings, "grantex_audience", "") or "").strip()
        or os.getenv("AGENTICORG_GRANTEX_AUDIENCE", "").strip()
    )


def _strict_runtime() -> bool:
    env = getattr(settings, "env", "development")
    return is_strict_runtime_env(env if isinstance(env, str) else "development")


def _is_grantex_token(token: str) -> bool:
    """True only for RS256 tokens whose unverified ``iss`` is the configured Grantex issuer."""
    parts = _unverified_parts(token)
    if parts is None:
        return False
    header, payload = parts
    if header.get("alg") != "RS256":
        return False
    expected_issuer = grantex_expected_issuer()
    if not expected_issuer:
        return False
    issuer = payload.get("iss")
    return isinstance(issuer, str) and issuer.rstrip("/") == expected_issuer


# ── Process-level JWKS cache keyed by (jwks_uri, kid) ─────────────────────

_jwks_lock = threading.Lock()
_jwks_keys: dict[tuple[str, str], tuple[float, Any]] = {}  # -> (expires_at, key)
_jwks_negative: dict[tuple[str, str], float] = {}  # -> expires_at


def _reset_jwks_cache() -> None:  # test hook
    with _jwks_lock:
        _jwks_keys.clear()
        _jwks_negative.clear()


def _cached_signing_key(jwks_uri: str, kid: str) -> Any:
    """Return the RSA key for ``kid`` from cache or a bounded JWKS fetch."""
    import httpx
    from jwt.algorithms import RSAAlgorithm

    cache_key = (jwks_uri, kid)
    now = time.monotonic()
    with _jwks_lock:
        hit = _jwks_keys.get(cache_key)
        if hit is not None and hit[0] > now:
            return hit[1]
        negative_until = _jwks_negative.get(cache_key)
        if negative_until is not None and negative_until > now:
            raise GrantexAuthError("unknown kid (negative cache)")

    try:
        resp = httpx.get(jwks_uri, timeout=_JWKS_FETCH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        jwks = resp.json()
    # enterprise-gate: broad-except-ok reason=jwks-fetch-failure-is-negatively-cached-and-rejects-the-token
    except Exception as exc:
        with _jwks_lock:
            _jwks_negative[cache_key] = time.monotonic() + _JWKS_NEGATIVE_TTL_SECONDS
        raise GrantexAuthError(f"JWKS fetch failed: {exc}") from exc

    raw_keys = jwks.get("keys", []) if isinstance(jwks, dict) else []
    matched = [
        key
        for key in raw_keys
        if isinstance(key, dict) and key.get("kid") == kid and key.get("kty") == "RSA"
    ]
    if len(matched) != 1:
        with _jwks_lock:
            _jwks_negative[cache_key] = time.monotonic() + _JWKS_NEGATIVE_TTL_SECONDS
        raise GrantexAuthError(f"no unique RSA key for kid={kid!r}")
    try:
        signing_key = RSAAlgorithm.from_jwk(matched[0])
    # enterprise-gate: broad-except-ok reason=malformed-jwk-is-negatively-cached-and-rejects-the-token
    except Exception as exc:
        with _jwks_lock:
            _jwks_negative[cache_key] = time.monotonic() + _JWKS_NEGATIVE_TTL_SECONDS
        raise GrantexAuthError(f"invalid JWK: {exc}") from exc
    with _jwks_lock:
        _jwks_keys[cache_key] = (time.monotonic() + _JWKS_TTL_SECONDS, signing_key)
    return signing_key


@dataclass(frozen=True)
class VerifiedGrantexToken:
    principal_id: str
    agent_did: str
    developer_id: str
    scopes: tuple[str, ...]
    grant_id: str
    delegation_depth: int
    issued_at: int


def _verify_grantex_token_sync(token: str, *, jwks_uri: str, issuer: str, audience: str) -> VerifiedGrantexToken:
    """Blocking verification (JWKS fetch + RSA verify). Run via ``asyncio.to_thread``."""
    import jwt

    parts = _unverified_parts(token)
    if parts is None:
        raise GrantexAuthError("malformed token")
    header, _payload = parts
    if header.get("alg") != "RS256":
        raise GrantexAuthError("unsupported alg")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise GrantexAuthError("missing kid")

    signing_key = _cached_signing_key(jwks_uri, kid)
    decode_kwargs: dict[str, Any] = {"algorithms": ["RS256"], "issuer": issuer}
    if audience:
        decode_kwargs["audience"] = audience
    else:
        decode_kwargs["options"] = {"verify_aud": False}
    try:
        claims = jwt.decode(token, signing_key, **decode_kwargs)
    except jwt.PyJWTError as exc:
        raise GrantexAuthError(f"grant token verification failed: {exc}") from exc

    for field in ("jti", "sub", "agt", "dev", "scp", "iat", "exp"):
        if field not in claims:
            raise GrantexAuthError(f"grant token missing claim {field}")
    scopes = claims.get("scp") or []
    if not isinstance(scopes, list | tuple):
        raise GrantexAuthError("scp must be a list")
    depth = claims.get("delegationDepth")
    return VerifiedGrantexToken(
        principal_id=str(claims["sub"]),
        agent_did=str(claims["agt"]),
        developer_id=str(claims["dev"]),
        scopes=tuple(str(s) for s in scopes),
        grant_id=str(claims.get("grnt") or claims["jti"]),
        delegation_depth=int(depth) if depth is not None else 0,
        issued_at=int(claims["iat"]),
    )


async def _resolve_agent_tenant(agent_did: str) -> tuple[str, str] | None:
    """Map a Grantex agent DID to ``(tenant_id, agent_id)`` via ``agents.config.grantex``."""
    from sqlalchemy import select

    from core.database import async_session_factory
    from core.models.agent import Agent

    async with async_session_factory() as session:
        result = await session.execute(
            select(Agent.id, Agent.tenant_id).where(
                Agent.config["grantex"]["grantex_did"].astext == agent_did,
            )
        )
        rows = result.all()
    if len(rows) != 1:
        return None
    agent_id, tenant_id = rows[0]
    return str(tenant_id), str(agent_id)


async def resolve_grantex_claims(token: str) -> dict[str, Any]:
    """Verify a Grantex grant token and bind it to a tenant.

    Raises ``GrantexAuthError`` (a ``ValueError``) on any failure. Never
    touches the network when Grantex is not configured for this deployment.
    """
    issuer = grantex_expected_issuer()
    audience = grantex_expected_audience()
    if not issuer:
        raise GrantexAuthError("grantex issuer not configured")
    if not audience:
        if _strict_runtime():
            raise GrantexAuthError("AGENTICORG_GRANTEX_AUDIENCE is required in strict runtimes")
        logger.warning("grantex_audience_unset_relaxed_runtime")
    if not _is_grantex_token(token):
        raise GrantexAuthError("token issuer is not the configured Grantex issuer")

    verified = await asyncio.to_thread(
        _verify_grantex_token_sync,
        token,
        jwks_uri=grantex_jwks_uri(),
        issuer=issuer,
        audience=audience,
    )
    mapping = await _resolve_agent_tenant(verified.agent_did)
    if mapping is None:
        logger.warning("grantex_agent_did_unmapped")
        raise GrantexAuthError("grant token agent is not registered with any tenant")
    tenant_id, agent_id = mapping
    return {
        "sub": verified.principal_id,
        "agenticorg:tenant_id": tenant_id,
        "grantex:scopes": list(verified.scopes),
        "agenticorg:agent_id": agent_id,
        "grantex:agent_did": verified.agent_did,
        "grantex:developer_id": verified.developer_id,
        "grantex:grant_id": verified.grant_id,
        "grantex:delegation_depth": verified.delegation_depth,
        "iat": verified.issued_at,
    }


async def check_user_session_state(tenant_id: str, claims: dict[str, Any]) -> str | None:
    """Return a rejection reason, ``"unavailable"``, or ``None`` when a legacy token is honoured.

    Shared by the HTTP middleware and the WebSocket feed so revocation is
    enforced on every legacy-token entry point.
    """
    email = claims.get("sub")
    if not tenant_id or not isinstance(email, str) or not email:
        return None
    try:
        uuid.UUID(str(tenant_id))
    except (TypeError, ValueError):
        return "invalid_tenant_claim"
    try:
        state = await get_user_session_state(tenant_id, email)
    except RuntimeError:
        # Strict runtime with both cache and DB unavailable: fail closed.
        logger.error("user_session_state_unavailable_strict")
        return "unavailable"
    # enterprise-gate: broad-except-ok reason=session-state-lookup-error-fails-closed-in-strict-runtime
    except Exception:
        logger.exception("user_session_state_lookup_error")
        return "unavailable" if _strict_runtime() else None
    issued_at = claims.get("iat")
    return state.rejects_token(float(issued_at) if isinstance(issued_at, int | float) else None)


class GrantexAuthMiddleware(BaseHTTPMiddleware):
    """Triple-mode auth: API keys + Grantex RS256 grant tokens + legacy HS256 JWTs."""

    EXEMPT_PATHS = {
        "/api/v1/health", "/api/v1/health/liveness", "/api/v1/auth/login",
        "/api/health",
        "/api/v1/auth/google", "/api/v1/auth/config", "/api/v1/auth/signup",
        "/api/v1/auth/forgot-password", "/api/v1/auth/reset-password",
        "/api/v1/org/accept-invite",
        "/api/v1/demo-request",
        "/api/v1/a2a/.well-known/agent.json",  # A2A discovery (public)
        "/api/v1/a2a/agent-card",  # A2A discovery alias (nginx-safe)
        "/api/v1/a2a/agents",  # A2A agent list (public)
        "/api/v1/mcp/tools",  # MCP tool discovery (public)
        "/api/v1/push/vapid-key",  # VAPID public key (browser needs before login)
        "/api/v1/billing/callback",  # Plural redirect callback (browser returning from gateway)
        "/api/v1/billing/callback/stripe",  # Stripe redirect callback
        "/api/v1/oauth/callback",  # Native connector OAuth callback (pre-session; state-validated)
        "/api/v1/billing/plans",  # Public pricing — no tenant data
        # Codex 2026-04-23 prod re-verification: /billing/health +
        # /knowledge/health are declared auth-free in their route
        # files, but GrantexAuthMiddleware still required a token.
        # Exempt them so ops smoke / uptime probes can call them.
        "/api/v1/billing/health",
        "/api/v1/knowledge/health",
        "/api/v1/branding",  # Public tenant branding for the login page
        "/api/v1/status",  # Public status page
        "/api/v1/product-facts",  # Public product counts/version for README, Landing, Pricing
        "/docs", "/openapi.json", "/redoc",
    }

    EXEMPT_PREFIXES = (
        "/api/v1/evals",
        "/api/v1/webhooks/",
        "/api/v1/voice/webhooks/twilio/",  # Twilio HMAC signature is verified by the route
        "/api/v1/aa/consent/callback",
        "/api/v1/billing/webhook/",  # Plural & Stripe server-to-server webhooks
        "/api/v1/client-portal/public/",  # Signed client portal invite/access tokens
        "/api/v1/auth/sso/",  # SSO login + OIDC callback (pre-session)
        "/api/v1/cron/",  # Cloud Scheduler triggers: X-Cron-Key verified by the route
    )

    async def _credential_failure_response(
        self,
        client_ip: str,
        detail: str = "Invalid or expired token",
    ) -> JSONResponse:
        if await is_ip_blocked(client_ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many failed attempts"},
            )
        blocked = await record_auth_failure(client_ip)
        if blocked:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many failed attempts"},
            )
        return JSONResponse(status_code=401, content={"detail": detail})

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in self.EXEMPT_PATHS or request.url.path.startswith(self.EXEMPT_PREFIXES):
            return await call_next(request)

        client_ip = resolve_client_ip(request)

        # Extract token. Explicit Authorization wins over ambient cookies
        # so API keys (ao_sk_...), SDKs, CI, and browser automation are
        # not broken by a stale or unrelated browser cookie jar. Browser
        # UI code no longer injects Authorization, so normal sessions
        # still use the HttpOnly cookie path.
        token = ""
        auth_header = request.headers.get("Authorization", "")
        if auth_header:
            if not auth_header.startswith("Bearer "):
                return await self._credential_failure_response(
                    client_ip,
                    "Unsupported Authorization scheme",
                )
            token = auth_header[7:].strip()
            if not token:
                return await self._credential_failure_response(client_ip)
        else:
            token = request.cookies.get("agenticorg_session") or ""
        if not token:
            # Missing credentials are expected for session-discovery probes
            # such as /auth/me on public pages; missing creds must stay 401
            # even if earlier malformed credentials blocked the source IP.
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing session cookie or Authorization header"},
            )

        # Downstream handlers must operate on the exact credential this
        # middleware authenticated. Re-reading cookies or headers in a route
        # can reverse the precedence above and create a cross-tenant split.
        request.state.auth_token = token
        request.state.auth_source = "authorization" if auth_header else "cookie"

        # Triple-mode: detect token type
        if token.startswith("ao_sk_"):
            return await self._handle_api_key(request, call_next, token, client_ip)
        elif _is_grantex_token(token):
            return await self._handle_grantex_token(request, call_next, token, client_ip)
        else:
            return await self._handle_legacy_token(request, call_next, token, client_ip)

    async def _handle_api_key(
        self, request: Request, call_next, token: str, client_ip: str
    ) -> Response:
        """Verify an API key (ao_sk_...) against the database."""
        try:
            from datetime import UTC, datetime

            import bcrypt as _bcrypt
            from sqlalchemy import select, update

            from core.database import async_session_factory
            from core.models.api_key import APIKey

            prefix = f"ao_sk_{token[6:12]}"

            async with async_session_factory() as session:
                result = await session.execute(
                    select(APIKey).where(
                        APIKey.prefix == prefix,
                        APIKey.status == "active",
                    )
                )
                candidates = result.scalars().all()

            def _match() -> Any:
                # bcrypt is CPU-bound (~100ms at cost 12): keep it off the loop.
                for candidate in candidates:
                    if _bcrypt.checkpw(token.encode(), candidate.key_hash.encode()):
                        return candidate
                return None

            matched_key = await asyncio.to_thread(_match) if candidates else None

            if not matched_key:
                return await self._credential_failure_response(
                    client_ip,
                    "Invalid API key",
                )

            # Check expiry
            if matched_key.expires_at and matched_key.expires_at < datetime.now(UTC):
                return await self._credential_failure_response(
                    client_ip,
                    "API key expired",
                )

            # Update last_used_at
            async with async_session_factory() as session:
                await session.execute(
                    update(APIKey)
                    .where(APIKey.id == matched_key.id)
                    .values(last_used_at=datetime.now(UTC))
                )
                await session.commit()

            # Set request state
            request.state.claims = {
                "sub": f"apikey:{matched_key.prefix}",
                "agenticorg:tenant_id": str(matched_key.tenant_id),
                "grantex:scopes": matched_key.scopes or [],
            }
            request.state.tenant_id = str(matched_key.tenant_id)
            request.state.scopes = matched_key.scopes or []
            request.state.agent_id = None
            request.state.user_sub = f"apikey:{matched_key.prefix}"
            request.state.auth_mode = "api_key"

            await clear_auth_failures(client_ip)
            return await call_next(request)

        # enterprise-gate: broad-except-ok reason=api-key-validation-boundary-records-failure-and-returns-401
        except Exception:
            logger.exception("API key validation error")
            return await self._credential_failure_response(
                client_ip,
                "API key validation failed",
            )

    async def _handle_grantex_token(
        self, request: Request, call_next, token: str, client_ip: str
    ) -> Response:
        """Verify a Grantex RS256 grant token and bind it to a registered agent's tenant."""
        try:
            claims = await resolve_grantex_claims(token)
        except GrantexAuthError as exc:
            logger.info("grantex_token_rejected", reason=str(exc))
            return await self._credential_failure_response(
                client_ip,
                "Invalid or expired grant token",
            )
        # enterprise-gate: broad-except-ok reason=grantex-token-validation-boundary-records-failure-and-returns-401
        except Exception:
            logger.exception("grantex_token_validation_error")
            return await self._credential_failure_response(
                client_ip,
                "Invalid or expired grant token",
            )

        request.state.claims = claims
        request.state.tenant_id = claims["agenticorg:tenant_id"]
        request.state.scopes = claims["grantex:scopes"]
        request.state.agent_id = claims["agenticorg:agent_id"]
        request.state.user_sub = claims["sub"]
        request.state.grant_token = token
        request.state.auth_mode = "grantex"

        await clear_auth_failures(client_ip)
        return await call_next(request)

    async def _handle_legacy_token(
        self, request: Request, call_next, token: str, client_ip: str
    ) -> Response:
        """Verify legacy HS256 JWT (existing auth flow)."""
        try:
            claims = await validate_token(token)
        except ValueError:
            return await self._credential_failure_response(client_ip)

        tenant_id = extract_tenant_id(claims)

        # Session revocation: a token is only as valid as its user row.
        # Deactivation / password reset / logout-all set
        # ``users.sessions_invalid_before``; tokens issued before it (or
        # for a non-active user) are rejected even though their signature
        # and expiry are fine.
        rejection = await check_user_session_state(tenant_id, claims)
        if rejection == "unavailable":
            return JSONResponse(
                status_code=503,
                content={"detail": "Session validation temporarily unavailable"},
            )
        if rejection is not None:
            logger.info("legacy_token_rejected", reason=rejection)
            return await self._credential_failure_response(client_ip, "Session is no longer valid")

        request.state.claims = claims
        request.state.tenant_id = tenant_id
        request.state.scopes = extract_scopes(claims)
        request.state.agent_id = claims.get("agenticorg:agent_id")
        request.state.user_sub = claims.get("sub", "")
        request.state.auth_mode = "legacy"

        # NOTE: ``request.path_params`` is always ``{}`` inside
        # BaseHTTPMiddleware (routing has not run yet), so a "tenant
        # mismatch" check here can never fire. Tenant binding is enforced
        # per-route via ``api.deps.get_current_tenant`` / tenant sessions.

        await clear_auth_failures(client_ip)
        return await call_next(request)

    # Auth failure tracking is now in core.auth_state (Redis-backed)
