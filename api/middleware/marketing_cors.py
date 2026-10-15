# SPDX-License-Identifier: Apache-2.0
"""Cross-site marketing intake without granting credentialed API access."""
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


class MarketingCORSMiddleware:
    def __init__(self, app: ASGIApp, **default_options):
        self.default = CORSMiddleware(app, **default_options)
        self.marketing = CORSMiddleware(
            app,
            allow_origins=["https://grantex.dev", "https://www.grantex.dev"],
            allow_methods=["POST"],
            allow_headers=["Content-Type"],
            allow_credentials=False,
            expose_headers=["X-Request-ID"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        origin = next((value for key, value in scope.get("headers", []) if key == b"origin"), b"")
        marketing = (
            scope["type"] == "http"
            and scope.get("path") == "/api/v1/demo-request"
            and scope.get("method") in {"POST", "OPTIONS"}
            and origin in {b"https://grantex.dev", b"https://www.grantex.dev"}
        )
        await (self.marketing if marketing else self.default)(scope, receive, send)
