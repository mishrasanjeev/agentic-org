"""Grantex-aware FastAPI auth middleware.

Supports dual-mode authentication:
  1. Legacy HS256 JWTs (existing users, backward-compatible)
  2. Grantex RS256 grant tokens (new agents, external callers via A2A/MCP)

The middleware detects the token type by checking the JWT header algorithm:
  - RS256 → Grantex grant token → verify via Grantex SDK
  - HS256 → Legacy local token → verify via existing jwt.py
"""

from __future__ import annotations

import os

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from auth.jwt import extract_scopes, extract_tenant_id, validate_token
from core.auth_state import clear_auth_failures, is_ip_blocked, record_auth_failure

logger = structlog.get_logger()


def _is_grantex_token(token: str) -> bool:
    """Check if a token is a Grantex RS256 grant token (vs legacy HS256)."""
    try:
        import base64
        import json

        # Decode JWT header without verification
        header_b64 = token.split(".")[0]
        # Add padding
        padding = 4 - len(header_b64) % 4
        if padding != 4:
            header_b64 += "=" * padding
        header = json.loads(base64.urlsafe_b64decode(header_b64))
        return header.get("alg") == "RS256"
    except Exception:
        return False


class GrantexAuthMiddleware(BaseHTTPMiddleware):
    """Dual-mode auth: Grantex RS256 grant tokens + legacy HS256 JWTs."""

    EXEMPT_PATHS = {
        "/api/v1/health", "/api/v1/health/liveness", "/api/v1/auth/login",
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
        "/api/v1/billing/plans",  # Public pricing — no tenant data
        "/api/v1/branding",  # Public tenant branding for the login page
        "/api/v1/status",  # Public status page
        "/api/v1/product-facts",  # Public product counts/version for README, Landing, Pricing
        "/docs", "/openapi.json", "/redoc",
    }

    EXEMPT_PREFIXES = (
        "/api/v1/evals",
        "/api/v1/webhooks/",
        "/api/v1/aa/consent/callback",
        "/api/v1/billing/webhook/",  # Plural & Stripe server-to-server webhooks
        "/api/v1/auth/sso/",  # SSO login + OIDC callback (pre-session)
    )

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in self.EXEMPT_PATHS or request.url.path.startswith(self.EXEMPT_PREFIXES):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # Check IP block (Redis-backed)
        if await is_ip_blocked(client_ip):
            return JSONResponse(status_code=429, content={"detail": "Too many failed attempts"})

        # Extract token — prefer the HttpOnly session cookie (CRITICAL-01
        # remediation). Fall back to Authorization: Bearer for API keys
        # (ao_sk_...), SDKs, CI, and browsers that have not yet migrated.
        token = ""
        cookie_token = request.cookies.get("agenticorg_session") or ""
        if cookie_token:
            token = cookie_token
        else:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        if not token:
            await record_auth_failure(client_ip)
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing session cookie or Authorization header"},
            )

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

            matched_key = None
            for candidate in candidates:
                if _bcrypt.checkpw(token.encode(), candidate.key_hash.encode()):
                    matched_key = candidate
                    break

            if not matched_key:
                await record_auth_failure(client_ip)
                return JSONResponse(
                    status_code=401, content={"detail": "Invalid API key"}
                )

            # Check expiry
            if matched_key.expires_at and matched_key.expires_at < datetime.now(UTC):
                await record_auth_failure(client_ip)
                return JSONResponse(
                    status_code=401, content={"detail": "API key expired"}
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

        except Exception:
            logger.exception("API key validation error")
            await record_auth_failure(client_ip)
            return JSONResponse(
                status_code=401, content={"detail": "API key validation failed"}
            )

    async def _handle_grantex_token(
        self, request: Request, call_next, token: str, client_ip: str
    ) -> Response:
        """Verify Grantex RS256 grant token."""
        try:
            from grantex._verify import VerifyGrantTokenOptions, verify_grant_token

            grantex_url = os.getenv("GRANTEX_BASE_URL", "https://api.grantex.dev")
            jwks_uri = f"{grantex_url}/.well-known/jwks.json"

            verified = verify_grant_token(token, VerifyGrantTokenOptions(
                jwks_uri=jwks_uri,
            ))

            # Set request state from Grantex claims
            request.state.claims = {
                "sub": getattr(verified, "principal_id", ""),
                "agenticorg:tenant_id": getattr(verified, "developer_id", ""),
                "grantex:scopes": getattr(verified, "scopes", []),
                "agenticorg:agent_id": getattr(verified, "agent_did", ""),
                "grantex:grant_id": getattr(verified, "grant_id", ""),
                "grantex:delegation_depth": getattr(verified, "delegation_depth", 0),
            }
            request.state.tenant_id = getattr(verified, "developer_id", "")
            request.state.scopes = getattr(verified, "scopes", [])
            request.state.agent_id = getattr(verified, "agent_did", "")
            request.state.user_sub = getattr(verified, "principal_id", "")
            request.state.grant_token = token
            request.state.auth_mode = "grantex"

            await clear_auth_failures(client_ip)
            return await call_next(request)

        except Exception:
            await record_auth_failure(client_ip)
            return JSONResponse(
                status_code=401, content={"detail": "Invalid or expired grant token"}
            )

    async def _handle_legacy_token(
        self, request: Request, call_next, token: str, client_ip: str
    ) -> Response:
        """Verify legacy HS256 JWT (existing auth flow)."""
        try:
            claims = await validate_token(token)
        except ValueError:
            await record_auth_failure(client_ip)
            return JSONResponse(
                status_code=401, content={"detail": "Invalid or expired token"}
            )

        tenant_id = extract_tenant_id(claims)
        request.state.claims = claims
        request.state.tenant_id = tenant_id
        request.state.scopes = extract_scopes(claims)
        request.state.agent_id = claims.get("agenticorg:agent_id")
        request.state.user_sub = claims.get("sub", "")
        request.state.auth_mode = "legacy"

        # Tenant mismatch check
        path_tenant = request.path_params.get("tenant_id")
        if path_tenant and path_tenant != tenant_id:
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "E4004", "message": "Tenant mismatch"}},
            )

        await clear_auth_failures(client_ip)
        return await call_next(request)

    # Auth failure tracking is now in core.auth_state (Redis-backed)
