"""Connector registry — register and discover connectors."""

from __future__ import annotations

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class ConnectorRegistry:
    _connectors: dict[str, type[BaseConnector]] = {}
    # Deprecated connectors that still resolve during a deprecation window, so stored
    # configurations, grants and agent tool references keep working. Each names the live
    # connector that replaces it. Left out of the catalog and the counts.
    _deprecated: dict[str, type[BaseConnector]] = {}
    _composio_tools: dict[str, dict] = {}  # tool_name -> metadata

    @classmethod
    def register(cls, connector_cls: type[BaseConnector]) -> None:
        cls._connectors[connector_cls.name] = connector_cls

    @classmethod
    def register_deprecated(cls, connector_cls: type[BaseConnector]) -> None:
        """Register a deprecated connector. The class names its live replacement in ``replacement``."""
        cls._deprecated[connector_cls.name] = connector_cls

    @classmethod
    def get(cls, name: str) -> type[BaseConnector] | None:
        return cls._connectors.get(name) or cls._deprecated.get(name)

    @classmethod
    def live_id(cls, name: str) -> str:
        """A deprecated id's replacement, else ``name`` itself.

        Grants for a deprecated connector's tools are issued under this id when the replacement
        has the same tool (``core.langgraph.tool_adapter._grant_connector_id``).
        """
        if name in cls._connectors:
            return name
        replacement = getattr(cls._deprecated.get(name), "replacement", None)
        return replacement if isinstance(replacement, str) and replacement in cls._connectors else name

    @classmethod
    def ids_of(cls, name: str) -> tuple[str, ...]:
        """``name`` first, then the connector's other ids: its live id and every deprecated id of it.

        A deprecated connector stays linked to its replacement during a deprecation window, so a
        grant that names any of these ids counts for the connector, for the tools the manifest
        under that id lists. An id with no other names gives ``(name,)``.
        """
        live = cls.live_id(name)
        retired = [old for old, c in cls._deprecated.items() if getattr(c, "replacement", None) == live]
        return tuple(dict.fromkeys([name, live, *retired]))

    @classmethod
    def all_names(cls, *, include_deprecated: bool = False) -> list[str]:
        names = list(cls._connectors.keys())
        if include_deprecated:
            names.extend(name for name in cls._deprecated if name not in cls._connectors)
        return names

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
