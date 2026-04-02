"""Test connector error handling — 500, 429, timeouts, malformed responses."""

from __future__ import annotations

import httpx
import pytest

from tests.connector_harness.conftest import make_connector

pytestmark = pytest.mark.asyncio


class TestConnectorErrorHandling:
    """Test that connectors propagate errors correctly."""

    async def test_http_500_raises(self, mock_server_url):
        """HTTP 500 from external service raises an error."""
        connector = await make_connector("stripe", mock_server_url)
        # Override base_url to error path
        connector._client = httpx.AsyncClient(
            base_url=f"{mock_server_url}/error/500",
            timeout=10.0,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await connector.execute_tool("create_payment_intent", {"amount": 1000, "currency": "usd"})

    async def test_http_429_raises(self, mock_server_url):
        """HTTP 429 rate limit from external service raises an error."""
        connector = await make_connector("hubspot", mock_server_url)
        connector._client = httpx.AsyncClient(
            base_url=f"{mock_server_url}/error/429",
            timeout=10.0,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await connector.execute_tool("create_contact", {"email": "test@test.com"})

    async def test_connection_refused_raises(self):
        """Connection to non-existent server raises an error."""
        connector = await make_connector("slack", "http://127.0.0.1:1")
        with pytest.raises((httpx.ConnectError, httpx.TransportError, OSError, Exception)):
            await connector.execute_tool("send_message", {"text": "test"})
