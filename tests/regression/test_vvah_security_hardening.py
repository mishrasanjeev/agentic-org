from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException


def test_shared_egress_guard_blocks_ip_literals_and_private_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.security.egress import EgressValidationError, validate_public_url

    monkeypatch.delenv("AGENTICORG_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    with pytest.raises(EgressValidationError, match="ip_literal"):
        validate_public_url("https://169.254.169.254/latest/meta-data", allowed_schemes=("https",))

    monkeypatch.setattr(
        "core.security.egress.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, "", ("10.0.0.5", 443))],
    )
    with pytest.raises(EgressValidationError, match="blocked_ip"):
        validate_public_url("https://tenant-controlled.example")


@pytest.mark.asyncio
async def test_pinned_dns_backend_connects_to_validated_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.security.egress import EgressValidationError, PinnedDnsAsyncNetworkBackend

    seen: list[str] = []

    class Delegate:
        async def connect_tcp(self, host: str, *_args, **_kwargs):
            seen.append(host)
            return object()

        async def sleep(self, _seconds: float) -> None:
            return None

    monkeypatch.setattr(
        "core.security.egress.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, "", ("93.184.216.34", 443))],
    )
    backend = PinnedDnsAsyncNetworkBackend(require_dns=True, delegate=Delegate())

    await backend.connect_tcp("tenant-controlled.example", 443)

    assert seen == ["93.184.216.34"]

    monkeypatch.setattr(
        "core.security.egress.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, "", ("10.0.0.5", 443))],
    )
    with pytest.raises(EgressValidationError, match="blocked_ip"):
        await backend.connect_tcp("tenant-controlled.example", 443)


def test_shopify_runtime_rejects_non_shopify_admin_domains() -> None:
    from api.v1 import commerce_runtime as commerce_runtime_api

    with pytest.raises(HTTPException, match="myshopify"):
        commerce_runtime_api._normalize_shopify_domain_for_api("https://169.254.169.254")

    with pytest.raises(HTTPException, match="myshopify"):
        commerce_runtime_api._normalize_shopify_domain_for_api("attacker.example.com")

    with pytest.raises(HTTPException, match="myshopify"):
        commerce_runtime_api._normalize_shopify_domain_for_api("myshopify.com")

    assert (
        commerce_runtime_api._normalize_shopify_domain_for_api("Good-Shop.myshopify.com/")
        == "good-shop.myshopify.com"
    )


@pytest.mark.asyncio
async def test_shopify_graphql_client_blocks_unsafe_domain_before_network() -> None:
    from core.commerce.c6z_runtime_vertical import (
        C6ZRuntimeValidationError,
        ShopifyAdminGraphQLClient,
        ShopifyCredentials,
    )

    client = ShopifyAdminGraphQLClient(
        ShopifyCredentials(
            shop_domain="169.254.169.254",
            admin_access_token="shpat_redacted_fixture",
            api_version="2026-04",
        ),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
    )
    with pytest.raises(C6ZRuntimeValidationError, match="unsafe Shopify"):
        await client.fetch_products(page_size=1, max_pages=1)


@pytest.mark.asyncio
async def test_oidc_discovery_rejects_rewritten_jwks_host() -> None:
    from auth.sso.oidc import OIDCProvider

    provider = OIDCProvider(
        provider_key="okta_test",
        config={
            "issuer": "https://example.com",
            "client_id": "client-abc",
            "client_secret": "shhh",
            "redirect_uri": "https://app.example.com/api/v1/auth/sso/okta_test/callback",
            "scopes": ["openid", "profile", "email"],
        },
    )
    discovery_doc = {
        "issuer": "https://example.com",
        "authorization_endpoint": "https://example.com/oauth2/v1/authorize",
        "token_endpoint": "https://example.com/oauth2/v1/token",
        "jwks_uri": "https://169.254.169.254/latest/meta-data",
    }
    with patch("httpx.AsyncClient") as mock_client_cls:
        client_ctx = mock_client_cls.return_value.__aenter__.return_value
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value=discovery_doc)
        client_ctx.get = AsyncMock(return_value=response)
        with pytest.raises(ValueError, match="jwks_uri"):
            await provider.prepare()


def test_tenant_ai_provider_config_blocks_bearer_exfiltration_targets() -> None:
    from api.v1 import tenant_ai_credentials as creds_api

    with pytest.raises(HTTPException, match="api.openai.com"):
        creds_api._validate_provider_config("openai", {"base_url": "https://attacker.example.com"})

    with pytest.raises(HTTPException, match="HTTPS public"):
        creds_api._validate_provider_config("openai_compatible", {"base_url": "http://127.0.0.1:8000"})

    with pytest.raises(HTTPException, match="must not contain secrets"):
        creds_api._validate_provider_config(
            "openai_compatible",
            {"base_url": "https://example.com", "api_key": "sk-leak"},
        )


@pytest.mark.asyncio
async def test_base_connector_validates_actual_request_url_before_send() -> None:
    from connectors.framework.base_connector import BaseConnector, _validate_connector_egress_url

    class StubConnector(BaseConnector):
        name = "stub"
        base_url = "https://example.com"

        def _register_tools(self) -> None:
            return None

        async def _authenticate(self) -> None:
            return None

    connector = StubConnector()
    await connector.connect()
    try:
        assert connector._client is not None
        with pytest.raises(ValueError, match="Connector egress URL"):
            await connector._client.get("http://169.254.169.254/latest/meta-data")
        with pytest.raises(ValueError, match="Connector egress URL"):
            await connector._get("http://169.254.169.254/latest/meta-data")
        with pytest.raises(ValueError, match="Connector egress URL"):
            _validate_connector_egress_url("http://example.com", connector_name="stub", require_dns=False)
    finally:
        await connector.disconnect()


def test_connector_retry_paths_do_not_recreate_raw_httpx_clients() -> None:
    repo = Path(__file__).resolve().parents[2]
    offenders = []
    for path in (repo / "connectors").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "self._client = httpx.AsyncClient" in text:
            offenders.append(str(path.relative_to(repo)))

    assert offenders == []


def test_provider_specific_host_builders_reject_ssrf_hosts() -> None:
    from connectors.finance.aa_consent import AAConsentManager
    from connectors.finance.oracle_fusion import OracleFusionConnector
    from connectors.marketing.mailchimp import MailchimpConnector
    from connectors.marketing.moengage import MoEngageConnector
    from connectors.ops.jira import JiraConnector

    with pytest.raises(ValueError, match="Finvu"):
        AAConsentManager(base_url="https://169.254.169.254/api/v1")
    with pytest.raises(ValueError, match="Finvu"):
        AAConsentManager(base_url="https://attacker.example/api/v1")
    assert AAConsentManager().base_url == "https://aa.finvu.in/api/v1"

    with pytest.raises(ValueError, match="Oracle Fusion instance"):
        OracleFusionConnector({"instance": "169.254.169.254"})
    assert (
        OracleFusionConnector({"instance": "tenant1"}).base_url
        == "https://tenant1.oraclecloud.com/fscmRestApi/resources/11.13.18.05"
    )
    with pytest.raises(ValueError, match="Mailchimp data center"):
        MailchimpConnector({"api_key": "fixture", "dc": "us21.evil"})
    with pytest.raises(ValueError, match="MoEngage datacenter"):
        MoEngageConnector({"datacenter": "01.evil"})
    with pytest.raises(ValueError, match="Jira domain"):
        JiraConnector({"domain": "tenant.atlassian.net.evil"})


def test_fixed_provider_connectors_ignore_untrusted_base_url_overrides() -> None:
    from connectors.comms.gmail import GmailConnector
    from connectors.comms.google_calendar import GoogleCalendarConnector
    from connectors.comms.slack import SlackConnector
    from connectors.comms.twilio import TwilioConnector
    from connectors.comms.whatsapp import WhatsappConnector
    from connectors.comms.youtube import YouTubeConnector
    from connectors.finance.netsuite import NetsuiteConnector
    from connectors.finance.quickbooks import QuickbooksConnector
    from connectors.hr.docusign import DocuSignConnector
    from connectors.hr.okta import OktaConnector
    from connectors.marketing.ga4 import GA4Connector
    from connectors.marketing.google_ads import GoogleAdsConnector
    from connectors.marketing.hubspot import HubspotConnector
    from connectors.marketing.linkedin_ads import LinkedinAdsConnector
    from connectors.marketing.mailchimp import MailchimpConnector
    from connectors.marketing.meta_ads import MetaAdsConnector
    from connectors.marketing.moengage import MoEngageConnector
    from connectors.marketing.salesforce import SalesforceConnector
    from connectors.ops.jira import JiraConnector
    from connectors.ops.pagerduty import PagerdutyConnector
    from connectors.ops.zendesk import ZendeskConnector

    attacker = {"base_url": "https://attacker.example"}
    cases = [
        (HubspotConnector(attacker), "https://api.hubapi.com"),
        (GmailConnector(attacker), "https://gmail.googleapis.com/gmail/v1"),
        (GoogleCalendarConnector(attacker), "https://www.googleapis.com/calendar/v3"),
        (YouTubeConnector(attacker), "https://www.googleapis.com/youtube/v3"),
        (QuickbooksConnector(attacker), "https://quickbooks.api.intuit.com/v3"),
        (SalesforceConnector(attacker), "https://org.my.salesforce.com/services/data/v60.0"),
        (GA4Connector(attacker), "https://analyticsdata.googleapis.com/v1beta"),
        (GoogleAdsConnector(attacker), "https://googleads.googleapis.com/v24"),
        (LinkedinAdsConnector(attacker), "https://api.linkedin.com/rest"),
        (MetaAdsConnector(attacker), "https://graph.facebook.com/v21.0"),
        (TwilioConnector(attacker), "https://api.twilio.com/2010-04-01"),
        (WhatsappConnector(attacker), "https://graph.facebook.com/v21.0"),
        (SlackConnector(attacker), "https://slack.com/api"),
        (DocuSignConnector(attacker), "https://na4.docusign.net/restapi/v2.1"),
        (OktaConnector(attacker), "https://org.okta.com/api/v1"),
        (OktaConnector({"base_url": "https://tenant1.okta.com/api/v1"}), "https://tenant1.okta.com/api/v1"),
        (PagerdutyConnector(attacker), "https://api.pagerduty.com"),
        (ZendeskConnector(attacker), "https://org.zendesk.com/api/v2"),
        (ZendeskConnector({"base_url": "https://tenant1.zendesk.com/api/v2"}), "https://tenant1.zendesk.com/api/v2"),
        (MailchimpConnector({**attacker, "api_key": "fixture-us21"}), "https://us21.api.mailchimp.com/3.0"),
        (MoEngageConnector({**attacker, "datacenter": "02"}), "https://api-02.moengage.com/v1"),
        (JiraConnector({**attacker, "domain": "tenant1"}), "https://tenant1.atlassian.net"),
        (
            NetsuiteConnector({**attacker, "account_id": "TSTDRV.1234.567"}),
            "https://tstdrv_1234_567.suitetalk.api.netsuite.com/services/rest/record/v1",
        ),
    ]

    for connector, expected_base_url in cases:
        assert connector.base_url == expected_base_url

    with pytest.raises(ValueError, match="NetSuite account_id"):
        NetsuiteConnector({"account_id": "tenant/evil"})


@pytest.mark.asyncio
async def test_tally_bridge_url_is_guarded_https_egress() -> None:
    from connectors.finance.tally import TallyBridgeError, TallyConnector

    connector = TallyConnector(
        {
            "bridge_url": "http://169.254.169.254/bridge",
            "bridge_id": "bridge-1",
            "bridge_token": "secret-token",
        }
    )

    with pytest.raises(TallyBridgeError, match="public HTTPS"):
        await connector._send_via_bridge("<ENVELOPE />")


def test_byo_ai_endpoint_changes_require_key_rotation_and_oauth_base_url_is_canonical() -> None:
    from api.v1 import connectors as connectors_api

    repo = Path(__file__).resolve().parents[2]
    tenant_ai_src = (repo / "api" / "v1" / "tenant_ai_credentials.py").read_text(encoding="utf-8")
    oauth_src = (repo / "api" / "v1" / "oauth_connector.py").read_text(encoding="utf-8")
    sso_src = (repo / "api" / "v1" / "sso.py").read_text(encoding="utf-8")
    connectors_src = (repo / "api" / "v1" / "connectors.py").read_text(encoding="utf-8")

    assert "Changing provider_config.base_url requires rotating api_key" in tenant_ai_src
    assert "current_base_url != next_base_url and body.api_key is None" in tenant_ai_src
    assert "base_url = fallback_base" in oauth_src
    assert "payload.get(\"base_url\") or fallback_base" not in oauth_src
    assert 'target.startswith("//")' in sso_src
    assert "_CONNECTOR_RETARGET_KEYS" in connectors_src
    assert "Changing connector endpoint/host requires rotating credentials" in connectors_src
    assert "new_secret_keys.intersection(_CONNECTOR_CREDENTIAL_ROTATION_KEYS)" in connectors_src
    assert "Endpoint/host changes are a credential boundary" in connectors_src
    assert "if not retarget_requested and cc and cc.credentials_encrypted" in connectors_src
    assert "bridge_url" in connectors_api._CONNECTOR_RETARGET_KEYS
    assert "bridge_token" in connectors_api._CONNECTOR_CREDENTIAL_ROTATION_KEYS
    assert "build_pinned_async_transport(require_dns=True)" in (
        repo / "connectors" / "framework" / "base_connector.py"
    ).read_text(encoding="utf-8")
    assert "transport=build_pinned_async_transport(require_dns=True)" in (
        repo / "connectors" / "finance" / "tally.py"
    ).read_text(encoding="utf-8")
    assert "username" not in connectors_api._CONNECTOR_CREDENTIAL_ROTATION_KEYS
    assert not connectors_api._canonicalize_connector_secret_urls(
        "sendgrid",
        connectors_api._clean_auth_config({"username": ""}),
    )
    assert not connectors_api._canonicalize_connector_secret_urls("hubspot", {})


def test_wordpress_site_normalization_does_not_silently_retarget_over_site() -> None:
    from connectors.marketing.wordpress import WordpressConnector

    connector = WordpressConnector(
        {
            "base_url": "https://attacker.example/wp-json/wp/v2",
            "site": "merchant.example",
        }
    )
    assert connector.base_url == "https://merchant.example/wp-json/wp/v2"

    with pytest.raises(ValueError, match="WordPress site"):
        WordpressConnector({"site": "http://merchant.example"})


def test_connector_refresh_urls_are_canonical_not_user_supplied() -> None:
    from api.v1 import connectors as connectors_api
    from core.tasks import token_refresh

    cleaned = connectors_api._canonicalize_connector_secret_urls(
        "hubspot",
        {
            "refresh_token": "refresh-fixture",
            "client_id": "client",
            "client_secret": "secret",
            "token_url": "https://attacker.example/token",
            "base_url": "https://attacker.example",
            "api_base_url": "https://attacker.example/api",
        },
    )
    assert cleaned["token_url"] == "https://api.hubapi.com/oauth/v1/token"
    assert "base_url" not in cleaned
    assert "api_base_url" not in cleaned

    assert (
        token_refresh._canonical_refresh_token_url("hubspot", {"token_url": "https://attacker.example/token"})
        == "https://api.hubapi.com/oauth/v1/token"
    )
    assert (
        token_refresh._canonical_refresh_token_url("zoho_books", {"region": "eu", "token_url": "https://evil.test"})
        == "https://accounts.zoho.eu/oauth/v2/token"
    )
    assert token_refresh._canonical_refresh_token_url("custom_oauth", {"token_url": "https://evil.test"}) is None


@pytest.mark.asyncio
async def test_plural_order_status_is_bound_to_authenticated_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import billing
    from core.billing import pinelabs_client

    pinelabs_client._order_map.clear()
    pinelabs_client._order_id_map.clear()
    monkeypatch.setattr(pinelabs_client.settings, "env", "development")
    monkeypatch.setattr(pinelabs_client, "_redis_client", lambda: None)
    pinelabs_client.store_order_mapping("aoabc", "v1-order-tenant-a", "tenant-a", "pro")
    monkeypatch.setattr(
        pinelabs_client,
        "get_order_status",
        lambda _order_id: {
            "order_id": "v1-order-tenant-a",
            "merchant_order_reference": "aoabc",
            "status": "PROCESSED",
        },
    )

    allowed = await billing.check_order_status(
        billing.OrderStatusRequest(order_id="v1-order-tenant-a"),
        tenant_id="tenant-a",
    )
    assert allowed["status"] == "PROCESSED"
    assert allowed["tenant_id"] == "tenant-a"

    with pytest.raises(HTTPException) as exc_info:
        await billing.check_order_status(
            billing.OrderStatusRequest(order_id="v1-order-tenant-a"),
            tenant_id="tenant-b",
        )
    assert exc_info.value.status_code == 404
