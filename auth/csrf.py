"""CSRF token generation + cookie helpers (PR-B / SEC-2026-05-P1-003).

The browser SPA authenticates via the HttpOnly ``agenticorg_session``
cookie. SameSite=lax helps but is not a complete CSRF defense — it
allows top-level GET/POST navigations from other origins, doesn't
reliably cover legacy browsers, and breaks down around redirects /
subdomain trust / payment flows.

The double-submit cookie pattern below pairs the HttpOnly session
cookie with a non-HttpOnly CSRF cookie + a header echo:

  Login response sets:
    Set-Cookie: agenticorg_session=...; HttpOnly; SameSite=Lax
    Set-Cookie: agenticorg_csrf=<token>;            SameSite=Lax  ← JS-readable

  SPA on every mutating fetch:
    Cookie: agenticorg_session=...; agenticorg_csrf=<token>
    X-CSRF-Token: <token>                                          ← from cookie

  Server checks (csrf_middleware.py):
    csrf cookie value == X-CSRF-Token header (constant-time compare)

A cross-site forgery cannot read the victim's ``agenticorg_csrf``
cookie (cross-origin reads are blocked by the SOP), so it cannot
echo the value into the header. The session cookie alone is then
useless for mutating requests.

Bearer-token API clients (SDKs, CI, MCP) don't carry browser cookies,
so they bypass this layer cleanly — covered explicitly in
``csrf_middleware.py``.
"""

from __future__ import annotations

import os
import secrets
from typing import Final

from fastapi import Response

from core.config import is_strict_runtime_env

CSRF_COOKIE_NAME: Final[str] = "agenticorg_csrf"
CSRF_HEADER_NAME: Final[str] = "X-CSRF-Token"

# 256-bit token. ``secrets.token_urlsafe(32)`` returns a 43-character
# URL-safe base64 string — short enough to ship in headers, long
# enough that brute-force is infeasible.
_CSRF_TOKEN_BYTES: Final[int] = 32


def generate_csrf_token() -> str:
    """Return a fresh URL-safe random CSRF token.

    Uses ``secrets`` (CSPRNG-backed) — never ``random`` (PRNG, predictable).
    """
    return secrets.token_urlsafe(_CSRF_TOKEN_BYTES)


def set_csrf_cookie(response: Response, token: str, max_age_seconds: int = 86400) -> None:
    """Attach the CSRF cookie to ``response``.

    Pairs with ``api.v1.auth._set_session_cookie``. Two divergences from
    the session cookie:

    1. ``httponly=False`` — the SPA's Axios interceptor must read this
       cookie to populate ``X-CSRF-Token`` on every mutating request.
       That's the whole point of double-submit.
    2. Same ``samesite='lax'`` + ``secure=is_prod`` — even though JS
       reads it, the browser still won't ship it cross-origin in the
       cases SameSite covers, providing defense-in-depth.
    """
    is_prod = is_strict_runtime_env(os.getenv("AGENTICORG_ENV", "development"))
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        max_age=max_age_seconds,
        httponly=False,  # SPA reads this — the double-submit contract
        secure=is_prod,
        samesite="lax",
        path="/",
    )


def clear_csrf_cookie(response: Response) -> None:
    """Clear the CSRF cookie on logout. Mirrors session-cookie cleanup."""
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/")
