"""FastAPI auth middleware — JWT validation, tenant context, rate limiting."""

from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from auth.jwt import extract_scopes, extract_tenant_id, validate_token
from core.auth_state import clear_auth_failures, is_ip_blocked, record_auth_failure


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate JWT, set tenant context, enforce rate limits."""

    EXEMPT_PATHS = {
        "/api/v1/health", "/api/v1/health/liveness", "/api/v1/auth/login",
        "/api/v1/auth/google", "/api/v1/auth/config", "/api/v1/auth/signup",
        "/api/v1/auth/forgot-password", "/api/v1/auth/reset-password",
        "/api/v1/org/accept-invite",
        "/api/v1/demo-request",
        "/api/v1/billing/callback",  # Plural redirect callback
        "/api/v1/billing/callback/stripe",  # Stripe redirect callback
        "/api/v1/billing/plans",  # Public pricing page
        "/api/v1/branding",  # Public tenant branding for the login page
        "/api/v1/status",  # Public status page
        "/api/v1/product-facts",  # Public product counts/version for README, Landing, Pricing
        "/docs", "/openapi.json", "/redoc",
    }

    EXEMPT_PREFIXES = (
        "/api/v1/evals",
        "/api/v1/billing/webhook/",  # Plural & Stripe webhooks
        "/api/v1/auth/sso/",  # SSO login + OIDC callback (no prior session)
    )

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip auth for health, docs, and public eval endpoints
        if request.url.path in self.EXEMPT_PATHS or request.url.path.startswith(self.EXEMPT_PREFIXES):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # Check if IP is blocked (Redis-backed)
        if await is_ip_blocked(client_ip):
            return JSONResponse(status_code=429, content={"detail": "Too many failed attempts"})

        # Extract token — prefer the HttpOnly session cookie (CRITICAL-01
        # remediation). Fall back to Authorization: Bearer for API
        # clients, CI, SDKs, and browsers that have not yet migrated.
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
        try:
            claims = await validate_token(token)
        except ValueError:
            await record_auth_failure(client_ip)
            return JSONResponse(
                status_code=401, content={"detail": "Invalid or expired token"}
            )

        # Set request state
        tenant_id = extract_tenant_id(claims)
        request.state.claims = claims
        request.state.tenant_id = tenant_id
        request.state.scopes = extract_scopes(claims)
        request.state.agent_id = claims.get("agenticorg:agent_id")
        request.state.user_sub = claims.get("sub", "")

        # Tenant mismatch check (E4004)
        path_tenant = request.path_params.get("tenant_id")
        if path_tenant and path_tenant != tenant_id:
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "E4004", "message": "Tenant mismatch"}},
            )

        await clear_auth_failures(client_ip)
        return await call_next(request)
