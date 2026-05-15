"""Regression pins for provider connector SSRF hardening."""

from __future__ import annotations

import pytest


class _FakeResponse:
    def __init__(self, body: dict[str, object]):
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._body


class _RecordingAsyncClient:
    def __init__(self, response_body: dict[str, object]):
        self.response_body = response_body
        self.base_url = ""
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self) -> _RecordingAsyncClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.posts.append((url, kwargs))
        return _FakeResponse(self.response_body)


def _client_factory(client: _RecordingAsyncClient):
    def _factory(*_args: object, **kwargs: object) -> _RecordingAsyncClient:
        client.base_url = str(kwargs.get("base_url", ""))
        return client

    return _factory


@pytest.mark.asyncio
async def test_google_connectors_ignore_configured_token_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import connectors.comms.gmail as gmail_module
    import connectors.comms.youtube as youtube_module
    from connectors.comms.gmail import GmailConnector
    from connectors.comms.youtube import YouTubeConnector

    gmail_client = _RecordingAsyncClient({"access_token": "gmail-token"})
    monkeypatch.setattr(gmail_module.httpx, "AsyncClient", _client_factory(gmail_client))

    gmail = GmailConnector(
        {
            "refresh_token": "refresh",
            "client_id": "client",
            "client_secret": "secret",
            "token_url": "https://169.254.169.254/latest/meta-data",
        }
    )
    await gmail._authenticate()
    assert gmail_client.posts[0][0] == gmail_module.GOOGLE_OAUTH_TOKEN_URL

    youtube_client = _RecordingAsyncClient({"access_token": "youtube-token"})
    monkeypatch.setattr(
        youtube_module.httpx, "AsyncClient", _client_factory(youtube_client)
    )

    youtube = YouTubeConnector(
        {
            "refresh_token": "refresh",
            "client_id": "client",
            "client_secret": "secret",
            "token_url": "https://attacker.test/oauth/token",
        }
    )
    await youtube._authenticate()
    assert youtube_client.posts[0][0] == youtube_module.GOOGLE_OAUTH_TOKEN_URL


@pytest.mark.asyncio
async def test_banking_aa_uses_fixed_finvu_token_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import connectors.finance.banking_aa as banking_module
    from connectors.finance.banking_aa import BankingAaConnector

    client = _RecordingAsyncClient({"access_token": "aa-token"})
    monkeypatch.setattr(banking_module.httpx, "AsyncClient", _client_factory(client))

    connector = BankingAaConnector(
        {
            "client_id": "client",
            "client_secret": "secret",
            "base_url": "https://metadata.google.internal",
            "token_url": "https://attacker.test/oauth/token",
        }
    )
    assert connector.base_url == banking_module.FINVU_API_BASE_URL

    await connector._authenticate()
    assert client.posts[0][0] == banking_module.FINVU_TOKEN_URL


@pytest.mark.asyncio
async def test_servicenow_requires_instance_label_and_derives_provider_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import connectors.ops.servicenow as servicenow_module
    from connectors.ops.servicenow import ServicenowConnector

    with pytest.raises(ValueError, match="single DNS label"):
        ServicenowConnector({"instance": "tenant.service-now.com#@169.254.169.254"})

    client = _RecordingAsyncClient({"access_token": "servicenow-token"})
    monkeypatch.setattr(
        servicenow_module.httpx, "AsyncClient", _client_factory(client)
    )

    connector = ServicenowConnector(
        {
            "instance": "tenant-1",
            "client_id": "client",
            "client_secret": "secret",
            "username": "user",
            "password": "pass",
        }
    )
    assert connector.base_url == "https://tenant-1.service-now.com/api/now"

    await connector._authenticate()
    assert client.base_url == "https://tenant-1.service-now.com"
    assert client.posts[0][0] == "/oauth_token.do"


@pytest.mark.asyncio
async def test_sap_requires_safe_subdomain_and_ignores_token_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import connectors.finance.sap as sap_module
    from connectors.finance.sap import SapConnector

    with pytest.raises(ValueError, match="single DNS label"):
        SapConnector({"host": "tenant.s4hana.cloud.sap#evil"})

    connector = SapConnector(
        {
            "host": "tenant-1",
            "subdomain": "tenant-1",
            "region": "eu10",
            "client_id": "client",
            "client_secret": "secret",
            "token_url": "https://attacker.test/oauth/token",
        }
    )
    assert connector.base_url == "https://tenant-1.s4hana.cloud.sap/sap/opu/odata/sap"

    client = _RecordingAsyncClient({"access_token": "sap-token"})
    monkeypatch.setattr(sap_module.httpx, "AsyncClient", _client_factory(client))

    await connector._authenticate()
    assert client.base_url == "https://tenant-1.authentication.eu10.hana.ondemand.com"
    assert client.posts[0][0] == "/oauth/token"


@pytest.mark.asyncio
async def test_salesforce_uses_known_login_hosts_and_validates_instance_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import connectors.marketing.salesforce as salesforce_module
    from connectors.marketing.salesforce import SalesforceConnector

    client = _RecordingAsyncClient(
        {"instance_url": "https://acme.my.salesforce.com", "access_token": "sf-token"}
    )
    monkeypatch.setattr(
        salesforce_module.httpx, "AsyncClient", _client_factory(client)
    )

    connector = SalesforceConnector(
        {
            "client_id": "client",
            "client_secret": "secret",
            "login_url": "https://attacker.test/services/oauth2/token",
        }
    )
    await connector._authenticate()
    assert client.posts[0][0] == salesforce_module.SALESFORCE_LOGIN_URLS["production"]
    assert connector.base_url == "https://acme.my.salesforce.com/services/data/v60.0"

    blocked_client = _RecordingAsyncClient(
        {"instance_url": "https://169.254.169.254", "access_token": "sf-token"}
    )
    monkeypatch.setattr(
        salesforce_module.httpx, "AsyncClient", _client_factory(blocked_client)
    )

    blocked = SalesforceConnector({"client_id": "client", "client_secret": "secret"})
    with pytest.raises(ValueError, match="allowed provider host"):
        await blocked._authenticate()


@pytest.mark.asyncio
async def test_fixed_provider_connectors_ignore_base_url_in_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import connectors.finance.gstn as gstn_module
    import connectors.marketing.brandwatch as brandwatch_module
    from connectors.finance.gstn import GstnConnector
    from connectors.marketing.brandwatch import BrandwatchConnector

    gstn_client = _RecordingAsyncClient({"auth-token": "gstn-token"})
    monkeypatch.setattr(gstn_module.httpx, "AsyncClient", _client_factory(gstn_client))
    gstn = GstnConnector(
        {
            "api_key": "api",
            "gstin": "29ABCDE1234F1Z5",
            "username": "user",
            "password": "pass",
            "base_url": "https://169.254.169.254",
        }
    )
    assert gstn.base_url == gstn_module.GSTN_API_BASE_URL

    await gstn._authenticate()
    assert gstn_client.posts[0][0] == f"{gstn_module.GSTN_API_BASE_URL}/authenticate"

    brandwatch_client = _RecordingAsyncClient({"access_token": "brandwatch-token"})
    monkeypatch.setattr(
        brandwatch_module.httpx, "AsyncClient", _client_factory(brandwatch_client)
    )
    brandwatch = BrandwatchConnector(
        {
            "username": "user",
            "password": "pass",
            "base_url": "https://attacker.test",
        }
    )
    assert brandwatch.base_url == brandwatch_module.BRANDWATCH_API_BASE_URL

    await brandwatch._authenticate()
    assert (
        brandwatch_client.posts[0][0]
        == f"{brandwatch_module.BRANDWATCH_API_BASE_URL}/oauth/token"
    )
