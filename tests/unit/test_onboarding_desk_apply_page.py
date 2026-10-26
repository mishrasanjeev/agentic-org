# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The onboarding desk's applicant page: branding for the page, the session it opens, and cross-site access."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware.marketing_cors import APPLICANT_PREFIX, MarketingCORSMiddleware
from core.config import settings
from core.onboarding_desk import applications, branding, store, tokens

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()
LEAD = uuid.uuid4()


class TestPageBranding:
    def test_colour_and_privacy_link(self):
        raw = {"brand_color": "#1a73e8", "privacy_url": "https://northwind.example/privacy/"}
        page = branding.branding_from(raw)
        assert page.brand_color == "#1A73E8" and page.privacy_url == "https://northwind.example/privacy"
        assert page.to_dict()["brand_color"] == "#1A73E8"
        assert branding.branding_from(page.to_dict()) == page
        assert branding.branding_from({}).brand_color == "" and branding.branding_from({}).privacy_url == ""

    @pytest.mark.parametrize(
        ("raw", "field"),
        [
            ({"brand_color": "blue"}, "brand_color"),
            ({"brand_color": "#12345"}, "brand_color"),
            ({"brand_color": "#123456;x"}, "brand_color"),
            ({"privacy_url": "http://northwind.example/privacy"}, "privacy_url"),
            ({"privacy_url": "javascript:alert(1)"}, "privacy_url"),
            ({"privacy_url": "https://northwind.example/privacy?x=1"}, "privacy_url"),
            ({"privacy_url": "https://" + "a" * 300 + ".example"}, "privacy_url"),
        ],
    )
    def test_refused(self, raw, field):
        with pytest.raises(branding.BrandingError) as caught:
            branding.branding_from(raw)
        assert caught.value.field == field

    def test_the_session_carries_the_page_branding(self, monkeypatch):
        from api.v1 import onboarding_desk as api

        app = FastAPI()
        app.include_router(api.router, prefix="/api/v1")
        monkeypatch.setattr(settings, "onboarding_desk_enabled", True)

        async def open_for_lead(tid, lid, *, now=None):
            return SimpleNamespace(id=uuid.uuid4())

        async def state(tid, row, *, operator=False):
            return {"locale": "en"}

        async def get_branding(tid):
            return branding.branding_from(
                {"partner_name": "Northwind", "brand_color": "#0B57D0", "privacy_url": "https://n.example/privacy"}
            )

        monkeypatch.setattr(applications, "open_for_lead", open_for_lead)
        monkeypatch.setattr(applications, "state", state)
        monkeypatch.setattr(store, "get_branding", get_branding)
        body = TestClient(app).post(
            "/api/v1/onboarding-desk/apply/session", json={"t": tokens.sign("apply", TENANT, LEAD)}
        ).json()
        assert body["brand_color"] == "#0B57D0" and body["privacy_url"] == "https://n.example/privacy"
        assert body["partner_name"] == "Northwind"


def cors_client() -> TestClient:
    app = FastAPI()

    @app.put("/api/v1/onboarding-desk/apply/answers")
    async def answers():
        return {"ok": True}

    @app.get("/api/v1/onboarding-desk/applications")
    async def operator():
        return {"ok": True}

    app.add_middleware(
        MarketingCORSMiddleware,
        allow_origins=["https://app.agenticorg.ai"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return TestClient(app)


class TestCrossSiteAccess:
    def test_any_partner_site_may_call_the_applicant_routes_without_credentials(self):
        client = cors_client()
        origin = {"Origin": "https://partner.example"}
        preflight = origin | {
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type,x-applicant-session",
        }
        response = client.options("/api/v1/onboarding-desk/apply/answers", headers=preflight)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"
        assert "x-applicant-session" in response.headers["access-control-allow-headers"].lower()
        assert "access-control-allow-credentials" not in response.headers
        response = client.put("/api/v1/onboarding-desk/apply/answers", headers=origin)
        assert response.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in response.headers
        refused = preflight | {"Access-Control-Request-Headers": "authorization"}
        assert client.options("/api/v1/onboarding-desk/apply/answers", headers=refused).status_code == 400

    def test_the_partner_routes_keep_the_platform_policy(self):
        client = cors_client()
        preflight = {"Origin": "https://partner.example", "Access-Control-Request-Method": "GET"}
        assert client.options("/api/v1/onboarding-desk/applications", headers=preflight).status_code == 400
        allowed = client.get("/api/v1/onboarding-desk/applications", headers={"Origin": "https://app.agenticorg.ai"})
        assert allowed.headers["access-control-allow-credentials"] == "true"
        assert APPLICANT_PREFIX == "/api/v1/onboarding-desk/apply/"


class TestPageFiles:
    def test_the_hosted_page_needs_no_inline_script(self):
        page = (ROOT / "ui" / "public" / "onboarding-desk" / "apply.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", page, flags=re.S)
        assert scripts == [""] and 'src="/onboarding-desk/apply.js"' in page
        assert '<meta name="robots" content="noindex, nofollow"' in page
        assert '<agenticorg-onboarding api-base="/api/v1">' in page

    def test_the_element_never_writes_markup_or_storage(self):
        source = (ROOT / "ui" / "public" / "onboarding-desk" / "apply.js").read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "localStorage",
                          "sessionStorage", "eval(", "new Function"):
            assert forbidden not in source, forbidden
        assert 'credentials: "omit"' in source
