"""Deprecation header middleware.

When we deprecate an API endpoint we add it to DEPRECATED_ENDPOINTS
below. This middleware then emits the IETF-standard `Deprecation` and
`Sunset` headers on every response from that endpoint so clients can
plan their migration.

Reference:
  - RFC 8594 — The Sunset HTTP Header
  - draft-ietf-httpapi-deprecation-header — Deprecation HTTP Header

Usage:
    from api.middleware.deprecation import DeprecationHeaderMiddleware
    app.add_middleware(DeprecationHeaderMiddleware)

To deprecate an endpoint, edit DEPRECATED_ENDPOINTS below. The path
match is a prefix match (so /api/v1/old/thing and /api/v1/old/thing/42
both match an entry of /api/v1/old/thing).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# path_prefix → (sunset_date_rfc1123, successor_link)
DEPRECATED_ENDPOINTS: dict[str, tuple[str, str]] = {
    # Example — uncomment once the replacement ships:
    # "/api/v1/kpis/legacy": (
    #     "Wed, 31 Dec 2026 23:59:59 GMT",
    #     "</api/v1/kpis>; rel=\"successor-version\"",
    # ),
}


class DeprecationHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        path = request.url.path
        for prefix, (sunset, link) in DEPRECATED_ENDPOINTS.items():
            if path == prefix or path.startswith(prefix + "/"):
                response.headers["Deprecation"] = "true"
                response.headers["Sunset"] = sunset
                response.headers["Link"] = link
                break

        return response
