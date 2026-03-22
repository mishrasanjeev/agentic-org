"""FastAPI auth middleware — JWT validation, tenant context, rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from auth.jwt import extract_scopes, extract_tenant_id, validate_token

# Rate limiting: track failed attempts per IP
_failed_attempts: dict[str, list[float]] = defaultdict(list)
_blocked_ips: dict[str, float] = {}
BLOCK_DURATION = 900  # 15 minutes
MAX_FAILURES = 10
FAILURE_WINDOW = 60  # 1 minute


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate JWT, set tenant context, enforce rate limits."""

    EXEMPT_PATHS = {
        "/api/v1/health", "/api/v1/health/liveness", "/api/v1/auth/login",
        "/api/v1/auth/google", "/api/v1/auth/config",
        "/api/v1/demo-request",
        "/docs", "/openapi.json", "/redoc",
    }

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip auth for health and docs
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # Check if IP is blocked
        if client_ip in _blocked_ips:
            if time.time() < _blocked_ips[client_ip]:
                return JSONResponse(status_code=429, content={"detail": "Too many failed attempts"})
            else:
                del _blocked_ips[client_ip]

        # Extract token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._record_failure(client_ip)
            return JSONResponse(
                status_code=401, content={"detail": "Missing or invalid Authorization header"}
            )

        token = auth_header[7:]
        try:
            claims = await validate_token(token)
        except ValueError as e:
            self._record_failure(client_ip)
            return JSONResponse(
                status_code=401, content={"detail": f"Token validation failed: {e}"}
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

        return await call_next(request)

    def _record_failure(self, ip: str) -> None:
        now = time.time()
        attempts = _failed_attempts[ip]
        # Remove old attempts
        _failed_attempts[ip] = [t for t in attempts if now - t < FAILURE_WINDOW]
        _failed_attempts[ip].append(now)
        if len(_failed_attempts[ip]) >= MAX_FAILURES:
            _blocked_ips[ip] = now + BLOCK_DURATION
