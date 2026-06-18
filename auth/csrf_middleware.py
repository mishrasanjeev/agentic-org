"""CSRF middleware — double-submit cookie enforcement for browser sessions.

SEC-2026-05-P1-003 (docs/BRUTAL_SECURITY_SCAN_2026-05-01.md).

This middleware enforces that every cookie-authenticated mutating request
(POST / PUT / PATCH / DELETE) presents a matching ``X-CSRF-Token`` header
whose value equals the ``agenticorg_csrf`` cookie value. See
``auth/csrf.py`` for the rationale + token issuance.

Bypass conditions (request unaffected):

- Safe HTTP methods (GET, HEAD, OPTIONS) — no state change, no CSRF risk.
- Explicit bearer-token API clients — bearer tokens require explicit
  per-request transport, so CSRF doesn't apply even if the client also
  has ambient browser cookies in its jar. SDKs, CI, browser automation,
  the MCP server, and any other bearer caller works unchanged.
- Webhook endpoints — they have provider HMAC signing (or the
  SEC-2026-05-P1-007 dev bypass guard for unsigned local dev). CSRF
  on webhooks is the wrong defense for the wrong threat model.
- Auth bootstrap endpoints — login / signup / SSO callback / invite
  acceptance — there's no prior session cookie to enforce against.

The middleware is positioned AFTER auth in the request chain (it runs
on already-authenticated requests) so we don't waste cycles checking
CSRF on requests that will fail auth anyway. See ``api/main.py`` for
the wiring order.
"""

from __future__ import annotations

import secrets
from typing import Final

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from auth.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

_UNSAFE_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Path PREFIXES that bypass CSRF. Each entry must have a non-CSRF
# defense in place (HMAC signing, OAuth state, rate-limiting + auth
# throttling, etc.) — list updates require justification in code review.
_EXEMPT_PREFIXES: Final[tuple[str, ...]] = (
    "/api/v1/webhooks/",          # provider HMAC signing
    "/api/v1/billing/webhook/",   # Plural & Stripe HMAC
    "/api/v1/aa/consent/callback",  # SEC-004 will sign this in PR-C
)

# Exact path EXEMPTIONS — auth bootstrap endpoints with no prior session.
#
# These MUST stay aligned with ``auth.middleware.AuthMiddleware.EXEMPT_PATHS``
# for every auth-bootstrap route. The two middlewares are independent — a
# route that skips auth but is still CSRF-checked will reject a fresh
# browser whose only crime is having a stale session cookie from a prior
# tab. BUG-09 (Uday CA Firms 2026-05-02): "Sign in with Google" was
# rejected with SEC-2026-05-P1-003 because /auth/google was missing here
# while present in the auth exempt list. Same hole existed for
# /forgot-password and /reset-password — neither requires (or can
# usefully verify) a prior session, and both are POST so they triggered
# the middleware.
_EXEMPT_PATHS: Final[frozenset[str]] = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/signup",
    "/api/v1/auth/google",            # Google ID-token verification (no prior session)
    "/api/v1/auth/forgot-password",   # public — issues reset email
    "/api/v1/auth/reset-password",    # public — consumes one-time code
    "/api/v1/auth/sso/callback",
    "/api/v1/auth/sso/login",
    "/api/v1/auth/refresh",       # session refresh — verified by refresh-cookie + token
    "/api/v1/org/accept-invite",  # public invite bootstrap — consumes one-time code/JWT
})

# The session cookie name MUST stay in sync with ``api/v1/auth.py:
# _set_session_cookie``. Centralizing as a constant so a future rename
# breaks loudly here instead of silently disabling CSRF.
_SESSION_COOKIE_NAME: Final[str] = "agenticorg_session"


def _has_explicit_bearer(request: Request) -> bool:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    return bool(auth_header[7:].strip())


class CSRFMiddleware(BaseHTTPMiddleware):
    """Enforce double-submit CSRF tokens on browser-cookie sessions.

    The dispatch path is intentionally short — most requests bypass
    cleanly (safe methods, bearer-token clients, webhooks, auth
    bootstrap). Only cookie-authed mutating requests pay the cost of
    the constant-time header compare.
    """

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        if request.method not in _UNSAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if path in _EXEMPT_PATHS:
            return await call_next(request)
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await call_next(request)

        # Explicit bearer-token API clients bypass cleanly, even if a
        # browser-style cookie jar also contains an ambient session
        # cookie. Auth middleware runs before this middleware in the
        # full app and validates the bearer; CSRF is only a cookie-auth
        # browser defense.
        if _has_explicit_bearer(request):
            return await call_next(request)

        # Cookie-free API clients bypass cleanly.
        session_cookie = request.cookies.get(_SESSION_COOKIE_NAME)
        if not session_cookie:
            return await call_next(request)

        # Cookie-authed mutating request — enforce.
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
        if not csrf_cookie:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "Missing CSRF token cookie. Re-authenticate to "
                        "receive a fresh token. (SEC-2026-05-P1-003)"
                    ),
                },
            )

        header_token = request.headers.get(CSRF_HEADER_NAME, "")
        # Constant-time compare — avoid leaking token length / prefix
        # via timing oracles.
        if not secrets.compare_digest(csrf_cookie, header_token):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "CSRF token mismatch. The X-CSRF-Token header "
                        "must equal the agenticorg_csrf cookie value. "
                        "(SEC-2026-05-P1-003)"
                    ),
                },
            )

        return await call_next(request)
