# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Cross-site marketing intake and the onboarding applicant page, without granting credentialed API access."""
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

#: The onboarding desk's applicant routes. They are called from the partner's own site, which embeds
#: the applicant element, and authenticate only with the applicant session header, never with
#: cookies, so any origin may call them and no browser credentials are ever sent.
APPLICANT_PREFIX = "/api/v1/onboarding-desk/apply/"


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
        self.applicant = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_methods=["GET", "PUT", "POST", "DELETE"],
            allow_headers=["Content-Type", "X-Applicant-Session"],
            allow_credentials=False,
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        origin = next((value for key, value in scope.get("headers", []) if key == b"origin"), b"")
        marketing = (
            scope["type"] == "http"
            and scope.get("path") == "/api/v1/demo-request"
            and scope.get("method") in {"POST", "OPTIONS"}
            and origin in {b"https://grantex.dev", b"https://www.grantex.dev"}
        )
        if marketing:
            await self.marketing(scope, receive, send)
        elif scope["type"] == "http" and str(scope.get("path", "")).startswith(APPLICANT_PREFIX):
            await self.applicant(scope, receive, send)
        else:
            await self.default(scope, receive, send)
