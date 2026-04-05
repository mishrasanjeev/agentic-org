"""Tests for Microsoft 365 connectors — Teams bot with Composio fallback."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestTeamsConnectorRegistered:
    """TeamsConnector should auto-register in the ConnectorRegistry on import."""

    def test_teams_connector_registered(self):
        import connectors.microsoft.teams_bot  # noqa: F401
        from connectors.registry import ConnectorRegistry

        assert ConnectorRegistry.get("microsoft_teams") is not None
        connector_cls = ConnectorRegistry.get("microsoft_teams")
        assert connector_cls.name == "microsoft_teams"
        assert connector_cls.category == "comms"


class TestSendMessageToolExists:
    """The Teams connector should have a send_message tool registered."""

    def test_send_message_tool_exists(self):
        from connectors.microsoft.teams_bot import TeamsConnector

        connector = TeamsConnector()
        assert "send_message" in connector._tool_registry
        assert "list_channels" in connector._tool_registry
        assert "respond_to_mention" in connector._tool_registry


class TestComposioFallbackWhenSdkMissing:
    """When botbuilder-core is not installed, the connector should fall back to Composio."""

    @pytest.mark.asyncio
    async def test_composio_fallback_when_sdk_missing(self):
        import connectors.microsoft.teams_bot as teams_mod
        from connectors.microsoft.teams_bot import TeamsConnector

        original_has_sdk = teams_mod._HAS_BOT_SDK
        teams_mod._HAS_BOT_SDK = False

        try:
            connector = TeamsConnector()
            # Don't call connect() — we're testing the fallback path without a client

            # Mock the Composio discovery to return a Teams action
            mock_composio_tool = {
                "app": "microsoft_teams",
                "tool_name": "MICROSOFT_TEAMS_SEND_MESSAGE",
            }
            with patch(
                "connectors.microsoft.teams_bot._get_composio_teams_action",
                return_value=mock_composio_tool,
            ):
                result = await connector.send_message(
                    channel_id="test-channel",
                    message="Hello from test",
                )

            assert result["status"] == "sent_via_composio"
            assert result["channel_id"] == "test-channel"
        finally:
            teams_mod._HAS_BOT_SDK = original_has_sdk


class TestMs365ToolsInRegistry:
    """All MS365 tools should be discoverable in the ConnectorRegistry."""

    def test_ms365_tools_in_registry(self):
        import connectors.microsoft.teams_bot  # noqa: F401
        from connectors.registry import ConnectorRegistry

        # Verify the connector is in the registry
        assert "microsoft_teams" in ConnectorRegistry.all_names()

        # Verify the connector class has the expected tools
        connector_cls = ConnectorRegistry.get("microsoft_teams")
        assert connector_cls is not None

        instance = connector_cls()
        expected_tools = {"send_message", "list_channels", "respond_to_mention"}
        actual_tools = set(instance._tool_registry.keys())
        assert expected_tools == actual_tools
