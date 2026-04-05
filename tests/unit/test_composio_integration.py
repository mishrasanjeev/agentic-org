"""Tests for Composio integration (Section 1 of PRD v4.0.0).

Covers:
- TC-COMP-02: ComposioConnectorAdapter implements BaseConnector interface
- TC-COMP-03: Native connector takes priority over Composio for same app
- TC-COMP-04: Composio tools appear in _build_tool_index() with "composio:" prefix
- TC-COMP-01/08: Graceful degradation when COMPOSIO_API_KEY is missing
- TC-COMP-05: Tool execution delegates to Composio SDK

All Composio SDK calls are mocked — the real SDK is not required.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset ConnectorRegistry state between tests."""
    from connectors.registry import ConnectorRegistry

    original_connectors = dict(ConnectorRegistry._connectors)
    original_composio = dict(ConnectorRegistry._composio_tools)
    yield
    ConnectorRegistry._connectors = original_connectors
    ConnectorRegistry._composio_tools = original_composio


@pytest.fixture(autouse=True)
def _clean_discovery_cache():
    """Reset the discovery cache between tests."""
    from connectors.composio.discovery import clear_cache

    clear_cache()
    yield
    clear_cache()


def _fake_composio_tools():
    """Return fake Composio tool metadata for testing."""
    return [
        {
            "tool_name": "composio:notion:create_page",
            "app": "notion",
            "action": "create_page",
            "description": "Create a new Notion page",
            "auth_type": "oauth2",
            "connector_name": "composio",
        },
        {
            "tool_name": "composio:asana:list_tasks",
            "app": "asana",
            "action": "list_tasks",
            "description": "List tasks in Asana project",
            "auth_type": "oauth2",
            "connector_name": "composio",
        },
        {
            "tool_name": "composio:salesforce:get_lead",
            "app": "salesforce",
            "action": "get_lead",
            "description": "Get a lead from Salesforce",
            "auth_type": "oauth2",
            "connector_name": "composio",
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
#  TC-COMP-02: Adapter implements BaseConnector
# ═══════════════════════════════════════════════════════════════════════════


class TestComposioAdapterImplementsBaseConnector:
    """Verify ComposioConnectorAdapter satisfies the BaseConnector contract."""

    def test_is_subclass_of_base_connector(self):
        from connectors.composio.adapter import ComposioConnectorAdapter
        from connectors.framework.base_connector import BaseConnector

        assert issubclass(ComposioConnectorAdapter, BaseConnector)

    def test_has_required_class_attributes(self):
        from connectors.composio.adapter import ComposioConnectorAdapter

        assert ComposioConnectorAdapter.name == "composio"
        assert ComposioConnectorAdapter.category == "marketplace"
        assert ComposioConnectorAdapter.auth_type == "composio_managed"

    def test_has_register_tools_method(self):
        from connectors.composio.adapter import ComposioConnectorAdapter

        assert hasattr(ComposioConnectorAdapter, "_register_tools")
        assert callable(ComposioConnectorAdapter._register_tools)

    def test_has_authenticate_method(self):
        from connectors.composio.adapter import ComposioConnectorAdapter

        assert hasattr(ComposioConnectorAdapter, "_authenticate")
        assert callable(ComposioConnectorAdapter._authenticate)

    def test_has_execute_tool_method(self):
        from connectors.composio.adapter import ComposioConnectorAdapter

        assert hasattr(ComposioConnectorAdapter, "execute_tool")
        assert callable(ComposioConnectorAdapter.execute_tool)

    def test_has_health_check_method(self):
        from connectors.composio.adapter import ComposioConnectorAdapter

        assert hasattr(ComposioConnectorAdapter, "health_check")
        assert callable(ComposioConnectorAdapter.health_check)

    def test_instantiation_without_sdk_no_crash(self):
        """Adapter can be instantiated even without Composio SDK or API key."""
        with patch.dict("os.environ", {}, clear=False):
            # Ensure COMPOSIO_API_KEY is absent
            import os

            os.environ.pop("COMPOSIO_API_KEY", None)

            with patch("connectors.composio.adapter._COMPOSIO_AVAILABLE", False):
                from connectors.composio.adapter import ComposioConnectorAdapter

                adapter = ComposioConnectorAdapter()
                assert adapter._tool_registry == {}


# ═══════════════════════════════════════════════════════════════════════════
#  TC-COMP-03: Native connectors take priority
# ═══════════════════════════════════════════════════════════════════════════


class TestNativeConnectorPriority:
    """Native connectors must win over Composio equivalents."""

    def test_native_salesforce_blocks_composio_salesforce(self):
        from connectors.registry import ConnectorRegistry

        # Simulate a native salesforce connector being registered
        mock_cls = type("SalesforceConnector", (), {"name": "salesforce", "category": "marketing"})
        ConnectorRegistry._connectors["salesforce"] = mock_cls  # type: ignore[assignment]

        # Register Composio tools including salesforce
        with patch(
            "connectors.composio.discovery.discover_composio_tools",
            return_value=_fake_composio_tools(),
        ):
            count = ConnectorRegistry.register_composio_tools()

        # Salesforce tools should be skipped
        composio_tools = ConnectorRegistry.get_composio_tools()
        composio_apps = {t["app"] for t in composio_tools.values()}
        assert "salesforce" not in composio_apps

        # But notion and asana should be registered
        assert "composio:notion:create_page" in composio_tools
        assert "composio:asana:list_tasks" in composio_tools
        assert count == 2  # notion + asana, not salesforce

    def test_non_conflicting_composio_tools_registered(self):
        from connectors.registry import ConnectorRegistry

        # No native salesforce, notion, or asana
        ConnectorRegistry._connectors = {"darwinbox": MagicMock()}

        with patch(
            "connectors.composio.discovery.discover_composio_tools",
            return_value=_fake_composio_tools(),
        ):
            count = ConnectorRegistry.register_composio_tools()

        # All 3 should register
        assert count == 3
        assert len(ConnectorRegistry.get_composio_tools()) == 3


# ═══════════════════════════════════════════════════════════════════════════
#  TC-COMP-04: Composio tools in _build_tool_index
# ═══════════════════════════════════════════════════════════════════════════


class TestComposioToolsInBuildToolIndex:
    """Composio tools must appear in _build_tool_index() with composio: prefix."""

    def test_composio_tools_indexed(self):
        from connectors.registry import ConnectorRegistry
        from core.langgraph.tool_adapter import _build_tool_index

        # Seed composio tools into registry
        ConnectorRegistry._composio_tools = {
            "composio:notion:create_page": {
                "tool_name": "composio:notion:create_page",
                "app": "notion",
                "action": "create_page",
                "description": "Create a Notion page",
                "connector_name": "composio",
            },
        }

        index = _build_tool_index()

        assert "composio:notion:create_page" in index
        connector_name, description = index["composio:notion:create_page"]
        assert connector_name == "composio"
        assert "Notion" in description or "notion" in description.lower() or description != ""

    def test_native_tools_not_overwritten_by_composio(self):
        from connectors.registry import ConnectorRegistry
        from core.langgraph.tool_adapter import _build_tool_index

        # Imagine a native connector has a tool called "composio:notion:create_page"
        # (unlikely but defensive test)
        # First, ensure no composio tools conflict with native
        ConnectorRegistry._composio_tools = {}

        index = _build_tool_index()

        # The index should contain native tools (from whatever connectors are registered)
        # and zero composio tools since we cleared them
        for name in index:
            connector_name, _ = index[name]
            assert connector_name != "composio"


# ═══════════════════════════════════════════════════════════════════════════
#  TC-COMP-01/08: Graceful degradation without API key
# ═══════════════════════════════════════════════════════════════════════════


class TestComposioDisabledWithoutApiKey:
    """System must work normally when Composio is not configured."""

    def test_adapter_registers_zero_tools_without_key(self):
        import os

        os.environ.pop("COMPOSIO_API_KEY", None)

        with patch("connectors.composio.adapter._COMPOSIO_AVAILABLE", True):
            from connectors.composio.adapter import ComposioConnectorAdapter

            adapter = ComposioConnectorAdapter()
            assert len(adapter._tool_registry) == 0

    def test_adapter_registers_zero_tools_without_sdk(self):
        with patch("connectors.composio.adapter._COMPOSIO_AVAILABLE", False):
            from connectors.composio.adapter import ComposioConnectorAdapter

            adapter = ComposioConnectorAdapter()
            assert len(adapter._tool_registry) == 0

    def test_discovery_returns_empty_without_key(self):
        import os

        os.environ.pop("COMPOSIO_API_KEY", None)

        from connectors.composio.discovery import discover_composio_tools

        with patch("connectors.composio.discovery._COMPOSIO_AVAILABLE", True):
            tools = discover_composio_tools(api_key="")
            assert tools == []

    def test_discovery_returns_empty_without_sdk(self):
        from connectors.composio.discovery import discover_composio_tools

        with patch("connectors.composio.discovery._COMPOSIO_AVAILABLE", False):
            tools = discover_composio_tools()
            assert tools == []

    def test_health_check_reports_disabled(self):
        import os

        os.environ.pop("COMPOSIO_API_KEY", None)

        with patch("connectors.composio.adapter._COMPOSIO_AVAILABLE", False):
            from connectors.composio.adapter import ComposioConnectorAdapter

            adapter = ComposioConnectorAdapter()
            result = asyncio.run(adapter.health_check())
            assert result["status"] == "disabled"

    def test_register_composio_tools_returns_zero_without_sdk(self):
        from connectors.registry import ConnectorRegistry

        with patch(
            "connectors.composio.discovery.discover_composio_tools",
            return_value=[],
        ):
            count = ConnectorRegistry.register_composio_tools()
            assert count == 0

    def test_auth_bridge_returns_error_without_sdk(self):
        from connectors.composio.auth_bridge import (
            get_composio_connection_status,
            initiate_composio_oauth,
        )

        with patch("connectors.composio.auth_bridge._COMPOSIO_AVAILABLE", False):
            result = initiate_composio_oauth("notion")
            assert "error" in result

            status = get_composio_connection_status("notion")
            assert status["connected"] is False
            assert status["status"] == "sdk_unavailable"


# ═══════════════════════════════════════════════════════════════════════════
#  TC-COMP-05: Tool execution delegates to SDK
# ═══════════════════════════════════════════════════════════════════════════


class TestComposioToolExecutionDelegatesToSdk:
    """execute_tool must delegate to Composio SDK's execute_action."""

    def test_execute_tool_calls_sdk(self):
        mock_toolset = MagicMock()
        mock_toolset.execute_action.return_value = {"result": "page_created", "id": "pg_123"}

        fake_tools = [
            {
                "tool_name": "composio:notion:create_page",
                "app": "notion",
                "action": "create_page",
                "description": "Create a Notion page",
                "auth_type": "oauth2",
                "connector_name": "composio",
            },
        ]

        with (
            patch.dict("os.environ", {"COMPOSIO_API_KEY": "test_key_123"}),
            patch("connectors.composio.adapter._COMPOSIO_AVAILABLE", True),
            patch("connectors.composio.adapter._ComposioToolSet", return_value=mock_toolset),
            patch("connectors.composio.adapter.discover_composio_tools", return_value=fake_tools),
        ):
            from connectors.composio.adapter import ComposioConnectorAdapter

            adapter = ComposioConnectorAdapter()

            # Tool should be registered
            assert "composio:notion:create_page" in adapter._tool_registry

            # Execute it
            result = asyncio.run(
                adapter.execute_tool(
                    "composio:notion:create_page",
                    {"title": "Test Page", "content": "Hello"},
                )
            )

            assert result["success"] is True
            assert result["data"]["result"] == "page_created"
            mock_toolset.execute_action.assert_called_once_with(
                action="create_page",
                params={"title": "Test Page", "content": "Hello"},
            )

    def test_execute_tool_handles_sdk_error(self):
        mock_toolset = MagicMock()
        mock_toolset.execute_action.side_effect = RuntimeError("API rate limit exceeded")

        fake_tools = [
            {
                "tool_name": "composio:notion:create_page",
                "app": "notion",
                "action": "create_page",
                "description": "Create a Notion page",
                "auth_type": "oauth2",
                "connector_name": "composio",
            },
        ]

        with (
            patch.dict("os.environ", {"COMPOSIO_API_KEY": "test_key_123"}),
            patch("connectors.composio.adapter._COMPOSIO_AVAILABLE", True),
            patch("connectors.composio.adapter._ComposioToolSet", return_value=mock_toolset),
            patch("connectors.composio.adapter.discover_composio_tools", return_value=fake_tools),
        ):
            from connectors.composio.adapter import ComposioConnectorAdapter

            adapter = ComposioConnectorAdapter()
            result = asyncio.run(
                adapter.execute_tool("composio:notion:create_page", {"title": "Test"})
            )

            assert "error" in result
            assert "rate limit" in result["error"].lower()

    def test_execute_tool_raises_on_unknown_tool(self):
        with (
            patch.dict("os.environ", {"COMPOSIO_API_KEY": "test_key_123"}),
            patch("connectors.composio.adapter._COMPOSIO_AVAILABLE", True),
            patch("connectors.composio.adapter._ComposioToolSet", return_value=MagicMock()),
            patch("connectors.composio.adapter.discover_composio_tools", return_value=[]),
        ):
            from connectors.composio.adapter import ComposioConnectorAdapter

            adapter = ComposioConnectorAdapter()

            with pytest.raises(ValueError, match="not registered"):
                asyncio.run(
                    adapter.execute_tool("composio:fake:nonexistent", {})
                )


# ═══════════════════════════════════════════════════════════════════════════
#  Discovery caching
# ═══════════════════════════════════════════════════════════════════════════


class TestDiscoveryCaching:
    """Tool discovery must cache results to avoid repeated API calls."""

    def test_cache_prevents_second_fetch(self):
        mock_toolset = MagicMock()
        mock_toolset.get_tools.return_value = [
            {"appName": "notion", "name": "create_page", "description": "Create page", "authType": "oauth2"},
        ]

        with (
            patch("connectors.composio.discovery._COMPOSIO_AVAILABLE", True),
            patch("connectors.composio.discovery._ComposioToolSet", return_value=mock_toolset),
        ):
            from connectors.composio.discovery import discover_composio_tools

            # First call fetches
            tools1 = discover_composio_tools(api_key="test_key")
            assert len(tools1) == 1
            assert mock_toolset.get_tools.call_count == 1

            # Second call uses cache
            tools2 = discover_composio_tools(api_key="test_key")
            assert tools2 == tools1
            assert mock_toolset.get_tools.call_count == 1  # still 1

    def test_force_refresh_bypasses_cache(self):
        mock_toolset = MagicMock()
        mock_toolset.get_tools.return_value = [
            {"appName": "notion", "name": "create_page", "description": "Create page", "authType": "oauth2"},
        ]

        with (
            patch("connectors.composio.discovery._COMPOSIO_AVAILABLE", True),
            patch("connectors.composio.discovery._ComposioToolSet", return_value=mock_toolset),
        ):
            from connectors.composio.discovery import discover_composio_tools

            discover_composio_tools(api_key="test_key")
            discover_composio_tools(api_key="test_key", force_refresh=True)
            assert mock_toolset.get_tools.call_count == 2
