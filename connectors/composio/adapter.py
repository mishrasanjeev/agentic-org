"""ComposioConnectorAdapter — wraps Composio SDK tools as BaseConnector tools.

Extends BaseConnector so Composio tools participate in the standard
ConnectorRegistry / tool_adapter / Grantex pipeline.

If the Composio SDK is not installed or COMPOSIO_API_KEY is not set,
the adapter silently registers zero tools (no crash on startup).
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from connectors.composio.discovery import discover_composio_tools
from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()

# Guard all Composio SDK imports behind try/except.
# We assign to a module-level name so tests can patch it.
try:
    from composio import ComposioToolSet as _ComposioToolSet  # type: ignore[import-untyped]

    _COMPOSIO_AVAILABLE = True
except ImportError:
    _ComposioToolSet = None  # type: ignore[assignment,misc]
    _COMPOSIO_AVAILABLE = False


class ComposioConnectorAdapter(BaseConnector):
    """Adapter that exposes Composio SDK tools via the BaseConnector interface.

    Each discovered Composio tool is registered as
    ``composio:{app}:{action}`` in the tool registry.  Native connectors
    always take priority (checked at the registry level, not here).
    """

    name = "composio"
    category = "marketplace"
    auth_type = "composio_managed"
    base_url = ""
    rate_limit_rpm = 300
    timeout_ms = 30000

    def __init__(self, config: dict[str, Any] | None = None):
        self._composio_toolset: Any | None = None
        # BaseConnector.__init__ calls _register_tools()
        super().__init__(config)

    # ------------------------------------------------------------------
    # BaseConnector abstract methods
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        """Discover Composio tools and register each as a tool handler."""
        api_key = os.environ.get("COMPOSIO_API_KEY", "")

        if not _COMPOSIO_AVAILABLE:
            logger.info("composio_sdk_not_installed", msg="composio-core not installed; skipping tool registration")
            return

        if not api_key:
            logger.info("composio_api_key_missing", msg="COMPOSIO_API_KEY not set; skipping tool registration")
            return

        try:
            self._composio_toolset = _ComposioToolSet(api_key=api_key)
        except Exception as exc:
            logger.warning("composio_init_failed", error=str(exc))
            return

        tools = discover_composio_tools(api_key=api_key)
        for tool_meta in tools:
            tool_name = tool_meta["tool_name"]  # e.g. "composio:notion:create_page"
            self._tool_registry[tool_name] = self._make_handler(tool_meta)

        logger.info("composio_tools_registered", count=len(self._tool_registry))

    async def _authenticate(self) -> None:
        """Composio manages its own auth — nothing to set on _auth_headers."""
        self._auth_headers = {}

    async def health_check(self) -> dict[str, Any]:
        """Check if Composio SDK is functional."""
        if not _COMPOSIO_AVAILABLE:
            return {"status": "disabled", "reason": "composio-core not installed"}
        if not os.environ.get("COMPOSIO_API_KEY"):
            return {"status": "disabled", "reason": "COMPOSIO_API_KEY not set"}
        if self._composio_toolset is None:
            return {"status": "unhealthy", "reason": "toolset not initialised"}
        return {"status": "healthy", "tools_registered": len(self._tool_registry)}

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a Composio tool via the SDK.

        Delegates to Composio's execute_action method, which handles
        auth token refresh, rate-limiting, and retries internally.
        """
        if not _COMPOSIO_AVAILABLE or self._composio_toolset is None:
            return {"error": "Composio SDK not available"}

        handler = self._tool_registry.get(tool_name)
        if not handler:
            raise ValueError(f"Tool {tool_name} not registered on composio adapter")

        return await handler(**params)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_handler(self, tool_meta: dict[str, Any]):
        """Return an async callable that delegates to the Composio SDK."""
        app_name = tool_meta["app"]
        action_name = tool_meta["action"]
        description = tool_meta.get("description", "")

        async def _handler(**params: Any) -> dict[str, Any]:
            try:
                result = self._composio_toolset.execute_action(
                    action=action_name,
                    params=params,
                )
                return {"success": True, "data": result}
            except Exception as exc:
                logger.error(
                    "composio_execute_failed",
                    app=app_name,
                    action=action_name,
                    error=str(exc),
                )
                return {"error": f"Composio action failed: {exc}"}

        _handler.__name__ = f"composio_{app_name}_{action_name}"
        _handler.__doc__ = description or f"Execute {action_name} on {app_name} via Composio"
        return _handler
