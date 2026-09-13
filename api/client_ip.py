"""Client-IP resolution shared by every per-IP throttle.

Behind Cloud Run / nginx ``request.client.host`` is the proxy, so every user
collapses into one throttle bucket (login, signup, password reset, demo
requests and the auth-failure IP block). When ``settings.trust_proxy_headers``
(``AGENTICORG_TRUST_PROXY_HEADERS``) is enabled the LAST ``X-Forwarded-For``
hop is used instead: Cloud Run (and any well-behaved reverse proxy) appends
the peer address it actually saw to whatever the client sent, so a
client-supplied ``X-Forwarded-For: 1.2.3.4`` arrives as ``1.2.3.4, <real>``
and only the rightmost entry is trustworthy. Taking the first hop would let a
client pick its own throttle bucket. Off by default: without a trusted proxy
the header is entirely client-controlled.
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
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            return last
    return peer
