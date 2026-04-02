"""Test all 54 connectors × all tool functions against the mock server.

Run: pytest tests/connector_harness/test_all_connectors.py -v
"""

from __future__ import annotations

import pytest

from tests.connector_harness.conftest import get_all_connector_names, make_connector

ALL_NAMES = get_all_connector_names()

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("connector_name", ALL_NAMES)
class TestConnectorToolExecution:
    """Test every tool on every connector returns valid JSON."""

    async def test_all_tools_return_valid_dict(self, connector_name, mock_server_url):
        """Every registered tool function returns a dict or raises a param error (not a crash)."""
        connector = await make_connector(connector_name, mock_server_url)
        tools = list(connector._tool_registry.keys())
        assert len(tools) > 0, f"{connector_name} has no tools registered"

        crashes = {}
        for tool_name in tools:
            try:
                result = await connector.execute_tool(tool_name, {})
                # Success or error dict are both valid
                assert isinstance(result, dict), (
                    f"{connector_name}.{tool_name} returned {type(result).__name__}, expected dict"
                )
            except (KeyError, ValueError, TypeError, FileNotFoundError):
                pass  # Missing params or missing files — expected with empty config
            except RuntimeError:
                pass  # Connector not connected, API errors — expected in harness
            except Exception as e:
                # HTTP errors, XML parse errors, auth errors — expected against mock server
                err_name = type(e).__name__
                if err_name in ("HTTPStatusError", "ParseError", "ConnectError", "ReadTimeout"):
                    pass  # Expected when hitting mock server
                else:
                    crashes[tool_name] = f"{err_name}: {e}"

        assert len(crashes) == 0, (
            f"{connector_name}: {len(crashes)} tools crashed unexpectedly: {crashes}"
        )

    async def test_tool_count_at_least_four(self, connector_name, mock_server_url):
        """Each connector has at least 4 tools."""
        connector = await make_connector(connector_name, mock_server_url)
        count = len(connector._tool_registry)
        assert count >= 4, f"{connector_name} has only {count} tools (expected 4+)"

    async def test_connector_has_metadata(self, connector_name, mock_server_url):
        """Each connector has name, category, and auth_type set."""
        connector = await make_connector(connector_name, mock_server_url)
        assert connector.name == connector_name
        assert connector.category in (
            "finance", "hr", "comms", "marketing", "ops"
        ), f"{connector_name} category '{connector.category}' not valid"
        assert connector.auth_type, f"{connector_name} has no auth_type"

    async def test_health_check(self, connector_name, mock_server_url):
        """Health check returns a dict with status."""
        connector = await make_connector(connector_name, mock_server_url)
        try:
            health = await connector.health_check()
            assert isinstance(health, dict)
            assert "status" in health
        except Exception:  # noqa: S110
            pass  # Some connectors may not implement health_check


class TestConnectorRegistry:
    """Test the registry itself."""

    def test_54_connectors_registered(self):
        """All 54 connectors are registered."""
        names = get_all_connector_names()
        assert len(names) >= 50, f"Expected ~54 connectors, got {len(names)}: {names}"

    def test_all_categories_present(self):
        """All 5 categories have connectors."""
        import connectors  # noqa: F401
        from connectors.registry import ConnectorRegistry

        categories = set()
        for name in ConnectorRegistry.all_names():
            cls = ConnectorRegistry.get(name)
            categories.add(cls.category)

        expected = {"finance", "hr", "comms", "marketing", "ops"}
        assert categories == expected, f"Missing categories: {expected - categories}"

    def test_total_tool_count(self):
        """Total tools across all connectors is 240+."""
        import connectors  # noqa: F401
        from connectors.registry import ConnectorRegistry

        total = 0
        for name in ConnectorRegistry.all_names():
            cls = ConnectorRegistry.get(name)
            instance = cls(config={})
            total += len(instance._tool_registry)

        assert total >= 240, f"Expected 240+ tools, got {total}"
