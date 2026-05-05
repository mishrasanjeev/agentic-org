"""Regression pins for Ramesh/Aishwarya 2026-05-04 reports."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException


def test_oauth_authorization_url_has_offline_consent_and_no_client_secret() -> None:
    from api.v1.oauth_connector import OAUTH_PROVIDERS, _build_authorization_url

    url = _build_authorization_url(
        OAUTH_PROVIDERS["gmail"],
        client_id="client-id-123",
        redirect_uri="https://app.agenticorg.ai/api/v1/oauth/callback",
        state="opaque-state",
    )
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert params["client_id"] == ["client-id-123"]
    assert params["state"] == ["opaque-state"]
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert "gmail.modify" in params["scope"][0]
    assert "client_secret" not in parsed.query
    assert "refresh_token" not in parsed.query


def test_oauth_automation_covers_reported_native_connectors() -> None:
    from api.v1.oauth_connector import OAUTH_PROVIDERS

    assert {"gmail", "google_calendar", "youtube", "zoho_books"} <= set(OAUTH_PROVIDERS)
    for provider in OAUTH_PROVIDERS.values():
        assert provider.authorization_url.startswith("https://")
        assert provider.token_url.startswith("https://")
        assert provider.scopes


def test_oauth_automation_router_is_mounted() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "api" / "main.py").read_text(encoding="utf-8")
    assert "oauth_connector" in src
    assert "app.include_router(oauth_connector.router" in src


@pytest.mark.asyncio
async def test_oauth_state_storage_fails_closed_when_redis_missing(monkeypatch) -> None:
    from api.v1 import oauth_connector

    async def no_redis():
        return None

    monkeypatch.setattr(oauth_connector, "get_async_redis", no_redis)
    with pytest.raises(HTTPException) as exc:
        await oauth_connector._store_oauth_state(  # noqa: SLF001
            "opaque-state",
            __import__("uuid").UUID("11111111-1111-1111-1111-111111111111"),
            {"client_secret": "must-not-go-in-url"},
        )
    assert exc.value.status_code == 503
    assert "browser-visible state" in str(exc.value.detail)


def test_partner_dashboard_health_score_penalizes_overdue_filings() -> None:
    from api.v1.companies import _effective_client_health

    assert _effective_client_health(100, pending_filings=0, overdue_filings=0) == 100
    assert _effective_client_health(100, pending_filings=0, overdue_filings=1) == 75
    assert _effective_client_health(100, pending_filings=3, overdue_filings=2) == 35
    assert _effective_client_health(None, pending_filings=0, overdue_filings=5) == 0


def test_demo_request_ca_trial_fields_are_not_ignored_and_confirmation_sends(monkeypatch) -> None:
    import core.email as email_mod
    from api.v1.demo import DemoRequest, _send_trial_confirmation
    from core.test_doubles import fake_mail

    monkeypatch.setenv("AGENTICORG_TEST_FAKE_MAIL", "1")
    monkeypatch.setattr(email_mod, "_has_mx_record", lambda _domain: True)
    fake_mail.reset()

    body = DemoRequest(
        name="<Aishwarya>",
        email="aishwarya@agenticorg.ai",
        firm="Aishwarya CA LLP",
        clients="6-15",
        source="ca-firms-solution",
    )

    assert body.effective_company == "Aishwarya CA LLP"
    assert body.effective_role == "CA firm trial"
    assert _send_trial_confirmation(body) is True
    sent = fake_mail.last()
    assert sent is not None
    assert sent.to == "aishwarya@agenticorg.ai"
    assert "CA Firm trial request received" in sent.subject
    assert "<Aishwarya>" not in sent.html
    assert "&lt;Aishwarya&gt;" in sent.html


def test_partner_dashboard_source_uses_filing_units_for_overdue_labels() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "ui" / "src" / "pages" / "PartnerDashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert "partnerDashboard.overdueFilings" in src
    assert "overdue_filings" in src
    assert "{overdueFilings} clients" not in src
    assert "health_score: Number(c.health_score" in src
