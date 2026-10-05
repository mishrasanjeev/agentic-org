"""Gmail connector health must exercise an authenticated Gmail resource."""

from unittest.mock import AsyncMock

import httpx
import pytest

from connectors.comms.gmail import GmailConnector


@pytest.mark.asyncio
async def test_gmail_health_checks_profile_without_returning_mailbox_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = GmailConnector({"access_token": "synthetic-local-token"})
    seen_urls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer synthetic-local-token"
        return httpx.Response(200, json={"emailAddress": "user@example.test"})

    monkeypatch.setattr(connector, "_validate_request_url", lambda _method, _path: None)
    async with httpx.AsyncClient(
        base_url="https://gmail.googleapis.com/gmail/v1",
        headers={"Authorization": "Bearer synthetic-local-token"},
        transport=httpx.MockTransport(respond),
    ) as client:
        connector._client = client
        result = await connector.health_check()

    assert seen_urls == ["https://gmail.googleapis.com/gmail/v1/users/me/profile"]
    assert result == {"status": "healthy", "account_verified": True}


@pytest.mark.asyncio
async def test_gmail_health_rejects_invalid_profile_response() -> None:
    connector = GmailConnector({"access_token": "synthetic-local-token"})
    async with httpx.AsyncClient() as client:
        connector._client = client
        connector._get = AsyncMock(return_value={"messagesTotal": 5})  # type: ignore[method-assign]
        assert await connector.health_check() == {
            "status": "unhealthy",
            "reason": "invalid_profile_response",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "authentication_rejected"), (403, "insufficient_permissions")],
)
async def test_gmail_health_reports_rejected_grants_without_provider_payload(
    status: int, reason: str
) -> None:
    connector = GmailConnector({"access_token": "synthetic-local-token"})
    request = httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile")
    response = httpx.Response(status, request=request, json={"error": "private-provider-detail"})
    async with httpx.AsyncClient() as client:
        connector._client = client
        connector._get = AsyncMock(  # type: ignore[method-assign]
            side_effect=httpx.HTTPStatusError("upstream rejected", request=request, response=response)
        )
        result = await connector.health_check()

    assert result == {"status": "unhealthy", "http_status": status, "reason": reason}
    assert "private-provider-detail" not in str(result)


@pytest.mark.asyncio
async def test_gmail_health_requires_credentials_and_connection() -> None:
    connector = GmailConnector({})
    assert await connector.health_check() == {
        "status": "not_configured",
        "reason": "missing_credentials",
    }
    connector = GmailConnector({"access_token": "synthetic-local-token"})
    assert await connector.health_check() == {"status": "not_connected"}
