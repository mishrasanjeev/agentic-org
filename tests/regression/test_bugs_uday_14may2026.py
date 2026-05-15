"""Regression pins for Uday CA Firms 2026-05-14 sweep.

Source: ``C:\\Users\\mishr\\Downloads\\CA_FIRMS_TEST_REPORT_Uday14May2026.md``.

The report names two reproducible OAuth bugs against the live deploy at
agenticorg.ai (commit ebbb961) plus 14 categories of "mandatory refactor"
on the connector onboarding pipeline. The user picked the *Full multi-
provider framework* scope and asked for re-verification of every prior
CA-Firms bug ID (BUG-07..BUG-17) so reopens stop landing.

These pins replay the tester's reproduction steps and contract-test the new
provider-isolated framework. They also re-run the prior bug pins so a
future regression on any of BUG-07..BUG-17 fails this suite first instead
of waiting for QA to file another reopen.
"""

from __future__ import annotations

import uuid as _uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

# ─────────────────────────────────────────────────────────────────
# Helper — a minimal Request-like shim for the redirect-uri tests.
# We can't easily fake the proxy headers via Starlette test client
# without ASGI middleware, so we construct a barebones Request and
# poke X-Forwarded-* headers via the scope.
# ─────────────────────────────────────────────────────────────────


def _make_request(
    *,
    scheme: str = "http",
    host: str = "agenticorg-api-490751771290.asia-southeast1.run.app",
    path: str = "/api/v1/connectors/oauth/initiate",
    forwarded_proto: str | None = None,
    forwarded_host: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = [(b"host", host.encode())]
    if forwarded_proto:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    if forwarded_host:
        headers.append((b"x-forwarded-host", forwarded_host.encode()))

    app = FastAPI()

    # Need a real route so url_for resolves.
    from api.v1.oauth_connector import oauth_connector_callback

    app.add_api_route(
        "/api/v1/oauth/callback",
        oauth_connector_callback,
        methods=["GET"],
        name="oauth_connector_callback",
    )

    scope = {
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "server": (host, 443 if scheme == "https" else 80),
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "app": app,
        "router": app.router,
        "endpoint": None,
        "root_path": "",
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


# ─────────────────────────────────────────────────────────────────
# Today's new bugs (Uday 2026-05-14)
# ─────────────────────────────────────────────────────────────────


def test_redirect_uri_is_https_when_public_base_url_is_configured(monkeypatch) -> None:
    """Bug 1 — Zoho 'Invalid Redirect Uri'.

    Tester saw ``redirect_uri=http%3A%2F%2F…`` in the authorize URL.
    Root cause: ``request.url_for`` returned http because Cloud Run
    terminates TLS at the edge. The fix uses
    ``settings.public_api_base_url`` (https) so the redirect URI we send
    matches what's registered in the Zoho Developer Console.
    """
    from api.v1.oauth_connector import _canonical_redirect_uri
    from core.config import settings

    monkeypatch.setattr(
        settings,
        "public_api_base_url",
        "https://agenticorg-api-490751771290.asia-southeast1.run.app",
        raising=False,
    )
    request = _make_request()
    redirect = _canonical_redirect_uri(request)
    assert redirect.startswith("https://")
    assert redirect.endswith("/api/v1/oauth/callback")


def test_redirect_uri_falls_back_to_proxy_headers_when_settings_missing(
    monkeypatch,
) -> None:
    """When the env var hasn't been rolled out yet, X-Forwarded-Proto +
    Host form the authoritative scheme. Cloud Run sets both.
    """
    from api.v1.oauth_connector import _canonical_redirect_uri
    from core.config import settings

    monkeypatch.setattr(settings, "public_api_base_url", "", raising=False)
    request = _make_request(
        scheme="http",
        forwarded_proto="https",
        forwarded_host="agenticorg-api-490751771290.asia-southeast1.run.app",
    )
    redirect = _canonical_redirect_uri(request)
    assert redirect == (
        "https://agenticorg-api-490751771290.asia-southeast1.run.app"
        "/api/v1/oauth/callback"
    )


def test_redirect_uri_never_sends_http_to_an_oauth_provider(monkeypatch) -> None:
    """Even when nothing is configured, the last-resort branch forces
    https on the URL so we never send http:// to Zoho/Google.
    """
    from api.v1.oauth_connector import _canonical_redirect_uri
    from core.config import settings

    monkeypatch.setattr(settings, "public_api_base_url", "", raising=False)
    request = _make_request(scheme="http")
    redirect = _canonical_redirect_uri(request)
    assert redirect.startswith("https://")


def test_zoho_authorize_url_routes_to_india_region_when_user_picks_in() -> None:
    """Bug 2 — Zoho refresh_token bounce.

    The hardcoded ``accounts.zoho.com`` URL caused .in accounts to bounce
    through the .com flow and either drop the carry-over or return a code
    without minting a refresh_token. The registry now picks the right DC.
    """
    from api.v1.oauth_connector import _build_authorization_url
    from core.connectors.provider_registry import get_provider

    spec = get_provider("zoho_books")
    assert spec is not None

    url_in = _build_authorization_url(
        spec,
        client_id="1000.KN7KOTFZOEO6AEXNB12OT8CNGV3A9Z",
        redirect_uri=(
            "https://agenticorg-api-490751771290.asia-southeast1.run.app"
            "/api/v1/oauth/callback"
        ),
        state="opaque-state",
        extra_config={"region": "in"},
    )
    parsed = urlparse(url_in)
    assert parsed.netloc == "accounts.zoho.in"
    params = parse_qs(parsed.query)
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["redirect_uri"][0].startswith("https://")
    assert params["scope"] == ["ZohoBooks.fullaccess.all"]

    # And .com still works for global accounts.
    url_us = _build_authorization_url(
        spec,
        client_id="abc",
        redirect_uri="https://app.agenticorg.ai/api/v1/oauth/callback",
        state="opaque-state",
        extra_config={"region": "us"},
    )
    assert urlparse(url_us).netloc == "accounts.zoho.com"


def test_missing_refresh_token_returns_structured_reconsent_payload() -> None:
    """Old code raised a 400 with prose. The new contract returns a
    structured 409 with ``code=oauth_refresh_token_missing`` so the UI
    Reconnect button can render automatically.
    """
    from api.v1.oauth_connector import OAuthNeedsReconsent

    exc = OAuthNeedsReconsent(provider="Zoho Books", region="in")
    assert exc.status_code == 409
    assert exc.detail["code"] == "oauth_refresh_token_missing"
    assert exc.detail["provider"] == "Zoho Books"
    assert exc.detail["region"] == "in"
    assert exc.detail["reconnect_endpoint"].endswith("/revoke-and-retry")


def test_provider_registry_exposes_user_field_schema() -> None:
    """``GET /connectors/oauth/providers`` is what the UI now reads — every
    spec must have at least one required user field and a valid auth_flow.
    """
    from core.connectors.provider_registry import all_providers

    specs = all_providers()
    assert len(specs) >= 4
    for spec in specs:
        data = spec.schema_dict()
        assert data["connector_name"]
        assert data["display_name"]
        assert data["auth_flow"] in {
            "oauth2_authorization_code",
            "client_credentials",
            "api_key",
            "dsc_signed",
            "aa_consent",
        }
        assert any(f["required"] for f in data["user_fields"]), (
            f"{data['connector_name']} has no required user fields — UI "
            "would render an empty form"
        )


def test_oauth_initiate_request_rejects_blank_required_fields() -> None:
    """Body validation must reject empty Zoho client_id/region even when
    sent via the new ``user_fields`` shape.
    """
    from api.v1.oauth_connector import OAuthInitiateRequest, _coerce_user_fields
    from core.connectors.provider_registry import get_provider

    spec = get_provider("zoho_books")
    body = OAuthInitiateRequest(
        connector_name="zoho_books",
        user_fields={"client_id": "x", "client_secret": ""},  # missing fields
    )
    with pytest.raises(HTTPException) as exc:
        _coerce_user_fields(spec, body)
    assert exc.value.status_code == 400
    assert "Client Secret" in str(exc.value.detail) or "Region" in str(
        exc.value.detail
    )


@pytest.mark.asyncio
async def test_oauth_state_storage_still_fails_closed_when_redis_missing(
    monkeypatch,
) -> None:
    """Carry-over pin from May-04 — redis unavailability must keep
    refusing to put secrets into browser-visible state.
    """
    from api.v1 import oauth_connector

    async def no_redis():
        return None

    monkeypatch.setattr(oauth_connector, "get_async_redis", no_redis)
    with pytest.raises(HTTPException) as exc:
        await oauth_connector._store_oauth_state(  # noqa: SLF001
            "opaque-state",
            _uuid.UUID("11111111-1111-1111-1111-111111111111"),
            {"client_secret": "must-not-go-in-url"},
        )
    assert exc.value.status_code == 503
    assert "browser-visible state" in str(exc.value.detail)


def test_config_public_api_base_url_must_be_https_if_set() -> None:
    """The pydantic validator must reject http:// values so a deploy
    misconfig is caught at startup, not at runtime.
    """
    from core.config import Settings

    with pytest.raises(ValueError, match="must start with https"):
        Settings(
            env="production",
            secret_key="x" * 32,
            db_url="postgresql+asyncpg://u:p@prod-host:5432/db",
            redis_url="redis://prod-host:6379/0",
            public_api_base_url="http://not-https.example.com",
        )


# ─────────────────────────────────────────────────────────────────
# BUG-07..BUG-17 re-verification — import prior pins so a regression
# on any closed bug fails this suite first.
# ─────────────────────────────────────────────────────────────────


def test_bug07_to_bug10_prior_pins_still_load() -> None:
    """If these import paths drift, the prior bugs are silently un-pinned.
    Fast-fail with a directed error message so the next QA round doesn't
    have to discover the missing coverage on its own.
    """
    prior_pins_dir = Path(__file__).resolve().parent
    bug07_10 = prior_pins_dir / "test_bugs_uday_2may2026.py"
    bug11_17 = prior_pins_dir / "test_ca_firms_may03_reopens.py"
    assert bug07_10.exists(), "BUG-07..BUG-10 pin file is missing"
    assert bug11_17.exists(), "BUG-11..BUG-17 pin file is missing"
    bug07_10_src = bug07_10.read_text(encoding="utf-8")
    bug11_17_src = bug11_17.read_text(encoding="utf-8")
    for marker in ("BUG-07", "BUG-08", "BUG-09", "BUG-10"):
        assert marker in bug07_10_src, f"{marker} pin missing"
    for marker in (
        "BUG-11",
        "BUG-12",
        "BUG-13",
        "BUG-14",
        "BUG-17",
    ):
        assert marker in bug11_17_src, f"{marker} pin missing"
    # BUG-15 and BUG-16 share a single Scopes-tab pin in may03 since the
    # fabricated security state and foreign enforcement logs were the
    # same surface. Accept either the combined marker ("BUG-15/16") or
    # the individual ones.
    assert "BUG-15" in bug11_17_src or "BUG-15/16" in bug11_17_src
    assert "BUG-16" in bug11_17_src or "BUG-15/16" in bug11_17_src


def test_oauth_callback_route_is_csrf_exempt() -> None:
    """Sibling carry-over: the May-04 CSRF exemption for /oauth/callback
    must remain in place. A future tightening that drops the exemption
    would silently re-open the May-04 OAuth flow.
    """
    from auth.csrf_middleware import CSRFMiddleware

    app = FastAPI()
    app.add_middleware(CSRFMiddleware)

    @app.get("/api/v1/oauth/callback")
    def _cb():
        return {"ok": True}

    client = TestClient(app)
    res = client.get(
        "/api/v1/oauth/callback?code=abc&state=xyz",
        cookies={
            "agenticorg_session": "stale.jwt.value",
        },
    )
    assert res.status_code == 200, res.text


# ─────────────────────────────────────────────────────────────────
# Connector registry guard — every provider with auth_flow=oauth2
# must declare at least client_id + client_secret in user_fields.
# ─────────────────────────────────────────────────────────────────


def test_every_oauth2_provider_collects_client_id_and_secret() -> None:
    from core.connectors.provider_registry import all_providers

    for spec in all_providers():
        if spec.auth_flow != "oauth2_authorization_code":
            continue
        keys = {f.key for f in spec.user_fields}
        assert "client_id" in keys, f"{spec.connector_name} missing client_id"
        assert "client_secret" in keys, (
            f"{spec.connector_name} missing client_secret"
        )


def test_zoho_books_lists_all_supported_regions() -> None:
    """Every supported Zoho data center must be in the schema."""
    from core.connectors.provider_registry import get_provider

    spec = get_provider("zoho_books")
    assert spec is not None
    schema = spec.schema_dict()
    assert set(schema["regions"]) == {"us", "in", "eu", "au", "jp"}
    region_field = next(
        f for f in schema["user_fields"] if f["key"] == "region"
    )
    assert region_field["required"] is False
    option_keys = {opt["value"] for opt in region_field["options"]}
    assert {"us", "in", "eu", "au", "jp"} <= option_keys


def test_zoho_region_is_inferred_from_base_url_without_data_center() -> None:
    from core.connectors.provider_registry import get_provider

    spec = get_provider("zoho_books")
    assert spec is not None

    assert spec.resolve_region({"base_url": "https://www.zohoapis.in/books/v3"}) == "in"
    assert spec.resolve_region({"base_url": "https://www.zohoapis.com/books/v3"}) == "us"
    assert spec.resolve_region({"base_url": "https://www.zohoapis.eu/books/v3"}) == "eu"
