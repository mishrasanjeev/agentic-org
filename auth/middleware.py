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
        "/api/health",
        "/api/v1/auth/google", "/api/v1/auth/config", "/api/v1/auth/signup",
        "/api/v1/auth/forgot-password", "/api/v1/auth/reset-password",
        "/api/v1/org/accept-invite",
        "/api/v1/demo-request",
        "/api/v1/billing/callback",  # Plural redirect callback
        "/api/v1/billing/callback/stripe",  # Stripe redirect callback
        "/api/v1/oauth/callback",  # Native connector OAuth callback (pre-session; state-validated)
        "/api/v1/billing/plans",  # Public pricing page
        # Codex 2026-04-23 prod re-verification: the billing + knowledge
        # health probes were declared auth-free in their route files but
        # the global AuthMiddleware still required a token before the
        # handler could run, so ops smoke tests could not use them. Add
        # the explicit paths to EXEMPT_PATHS so the /health endpoints
        # are actually callable from CI / Deploy / Uptime probes.
        "/api/v1/billing/health",
        "/api/v1/knowledge/health",
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
        # Skip auth for health, docs, and public eval endpoints
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in self.EXEMPT_PATHS or request.url.path.startswith(self.EXEMPT_PREFIXES):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # Extract token. Explicit Authorization wins over ambient cookies
        # so API clients, CI, SDKs, and browser automation are not broken
        # by a stale or unrelated browser cookie jar. Browser UI code no
        # longer injects Authorization, so normal sessions still use the
        # HttpOnly cookie path.
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
            # Anonymous session probes are not credential failures. Public
            # pages call /auth/me to discover session state; missing creds
            # must stay 401 even if earlier malformed credentials blocked
            # the source IP.
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing session cookie or Authorization header"},
            )
        try:
            claims = await validate_token(token)
        except ValueError:
            return await self._credential_failure_response(client_ip)

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
