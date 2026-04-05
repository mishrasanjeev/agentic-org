"""Composio tool auto-discovery.

Queries the Composio SDK for all available tools and maps them
to our internal format.  Results are cached to avoid re-fetching
on every call.
"""

from __future__ import annotations

import os
import time
from typing import Any

import structlog

logger = structlog.get_logger()

# Guard Composio SDK import — assign to module-level name for testability
try:
    from composio import ComposioToolSet as _ComposioToolSet  # type: ignore[import-untyped]

    _COMPOSIO_AVAILABLE = True
except ImportError:
    _ComposioToolSet = None  # type: ignore[assignment,misc]
    _COMPOSIO_AVAILABLE = False

# Module-level cache
_cached_tools: list[dict[str, Any]] = []
_cache_timestamp: float = 0.0
_CACHE_TTL_SECONDS = 600  # 10 minutes


def discover_composio_tools(
    *,
    api_key: str | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Return a list of available Composio tools with metadata.

    Each entry is a dict with keys:
        - tool_name: str   — "composio:{app}:{action}"
        - app: str         — e.g. "notion", "asana", "workday"
        - action: str      — e.g. "create_page", "list_tasks"
        - description: str — human-readable description
        - auth_type: str   — "oauth2", "api_key", etc.
        - connector_name: str — always "composio"

    Returns an empty list if the SDK is unavailable or unconfigured.
    """
    global _cached_tools, _cache_timestamp

    # Check cache first
    if not force_refresh and _cached_tools and (time.monotonic() - _cache_timestamp) < _CACHE_TTL_SECONDS:
        return _cached_tools

    resolved_key = api_key or os.environ.get("COMPOSIO_API_KEY", "")

    if not _COMPOSIO_AVAILABLE:
        logger.debug("composio_discovery_skip", reason="sdk_not_installed")
        return []

    if not resolved_key:
        logger.debug("composio_discovery_skip", reason="no_api_key")
        return []

    try:
        toolset = _ComposioToolSet(api_key=resolved_key)
        raw_tools = toolset.get_tools()  # returns list of tool definitions
    except Exception as exc:
        logger.warning("composio_discovery_failed", error=str(exc))
        return _cached_tools  # return stale cache on failure

    tools: list[dict[str, Any]] = []
    for raw in raw_tools:
        app = _extract_field(raw, "appName", "app_name", default="unknown")
        action = _extract_field(raw, "name", "action", default="unknown_action")
        description = _extract_field(raw, "description", "desc", default="")
        auth_type = _extract_field(raw, "authType", "auth_type", default="unknown")

        tool_name = f"composio:{app}:{action}"
        tools.append(
            {
                "tool_name": tool_name,
                "app": app,
                "action": action,
                "description": description,
                "auth_type": auth_type,
                "connector_name": "composio",
            }
        )

    _cached_tools = tools
    _cache_timestamp = time.monotonic()

    logger.info("composio_tools_discovered", count=len(tools))
    return tools


def get_composio_tools_by_app(app_name: str, *, api_key: str | None = None) -> list[dict[str, Any]]:
    """Return all Composio tools for a specific app (e.g. 'notion')."""
    all_tools = discover_composio_tools(api_key=api_key)
    return [t for t in all_tools if t["app"].lower() == app_name.lower()]


def clear_cache() -> None:
    """Clear the cached tool list (useful for testing)."""
    global _cached_tools, _cache_timestamp
    _cached_tools = []
    _cache_timestamp = 0.0


def _extract_field(obj: Any, *keys: str, default: str = "") -> str:
    """Extract a field from a dict or object, trying multiple key names."""
    if isinstance(obj, dict):
        for key in keys:
            if key in obj:
                return str(obj[key])
    else:
        for key in keys:
            val = getattr(obj, key, None)
            if val is not None:
                return str(val)
    return default
