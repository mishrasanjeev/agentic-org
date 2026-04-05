"""Connector registry — register and discover connectors."""

from __future__ import annotations

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class ConnectorRegistry:
    _connectors: dict[str, type[BaseConnector]] = {}
    _composio_tools: dict[str, dict] = {}  # tool_name -> metadata

    @classmethod
    def register(cls, connector_cls: type[BaseConnector]) -> None:
        cls._connectors[connector_cls.name] = connector_cls

    @classmethod
    def get(cls, name: str) -> type[BaseConnector] | None:
        return cls._connectors.get(name)

    @classmethod
    def all_names(cls) -> list[str]:
        return list(cls._connectors.keys())

    @classmethod
    def by_category(cls, category: str) -> list[type[BaseConnector]]:
        return [c for c in cls._connectors.values() if c.category == category]

    @classmethod
    def register_composio_tools(cls) -> int:
        """Discover and register all Composio tools.

        Native connectors MUST take priority: if a Composio tool's app
        name matches an existing native connector (e.g. ``salesforce``),
        all tools for that app are skipped.

        Returns the number of Composio tools registered.
        """
        try:
            from connectors.composio.discovery import discover_composio_tools
        except ImportError:
            logger.debug("composio_discovery_import_failed")
            return 0

        composio_tools = discover_composio_tools()
        if not composio_tools:
            return 0

        # Collect native connector names for priority check
        native_names = {n.lower() for n in cls._connectors if n != "composio"}

        registered = 0
        for tool_meta in composio_tools:
            app = tool_meta["app"].lower()

            # Skip if we have a native connector for this app
            if app in native_names:
                logger.debug("composio_tool_skipped_native_priority", app=app, tool=tool_meta["tool_name"])
                continue

            tool_name = tool_meta["tool_name"]
            cls._composio_tools[tool_name] = tool_meta
            registered += 1

        logger.info("composio_tools_in_registry", registered=registered, skipped=len(composio_tools) - registered)
        return registered

    @classmethod
    def get_composio_tools(cls) -> dict[str, dict]:
        """Return all registered Composio tools."""
        return dict(cls._composio_tools)

    @classmethod
    def composio_tool_names(cls) -> list[str]:
        """Return names of all registered Composio tools."""
        return list(cls._composio_tools.keys())
