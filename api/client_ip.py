"""Client-IP resolution shared by every per-IP throttle.

Behind Cloud Run / nginx ``request.client.host`` is the proxy, so every user
collapses into one throttle bucket (login, signup, password reset, demo
requests and the auth-failure IP block). When ``settings.trust_proxy_headers``
(``AGENTICORG_TRUST_PROXY_HEADERS``) is enabled the first ``X-Forwarded-For``
hop is used instead. Off by default: the header is client-controlled unless a
trusted proxy in front of the app overwrites it.
"""

from __future__ import annotations

from fastapi import Request

from core.config import settings


def client_ip(request: Request) -> str:
    """Return the throttle key for ``request`` (peer address or trusted XFF hop)."""
    client = getattr(request, "client", None)
    peer = client.host if client else "unknown"
    if not bool(getattr(settings, "trust_proxy_headers", False)):
        return peer
    headers = getattr(request, "headers", None)
    forwarded = headers.get("x-forwarded-for", "") if headers is not None else ""
    if isinstance(forwarded, str) and forwarded.strip():
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return peer
