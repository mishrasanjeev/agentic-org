"""Composio auth bridge — maps Composio OAuth/API-key flows to AgenticOrg.

Provides helpers to initiate OAuth for Composio-managed apps and
check connection status.  All functions are safe to call when the
SDK is not installed (they return error dicts).
"""

from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger()

# Guard Composio SDK import
try:
    from composio import ComposioToolSet  # type: ignore[import-untyped]

    _COMPOSIO_AVAILABLE = True
except ImportError:
    _COMPOSIO_AVAILABLE = False


def _get_toolset(api_key: str | None = None) -> Any | None:
    """Return an initialised ComposioToolSet, or None if unavailable."""
    if not _COMPOSIO_AVAILABLE:
        return None
    resolved_key = api_key or os.environ.get("COMPOSIO_API_KEY", "")
    if not resolved_key:
        return None
    try:
        return ComposioToolSet(api_key=resolved_key)
    except Exception as exc:
        logger.warning("composio_auth_bridge_init_failed", error=str(exc))
        return None


def initiate_composio_oauth(
    app_name: str,
    *,
    api_key: str | None = None,
    redirect_url: str | None = None,
    entity_id: str = "default",
) -> dict[str, Any]:
    """Start an OAuth flow for a Composio-managed app.

    Returns a dict with:
        - url: str — the OAuth authorization URL to redirect the user to
        - connection_id: str — identifier for this pending connection
    Or an error dict if the SDK is unavailable.

    Args:
        app_name: Composio app identifier (e.g. "notion", "google_workspace").
        api_key: Override COMPOSIO_API_KEY env var.
        redirect_url: Where to redirect after OAuth completes.
        entity_id: Composio entity (user/tenant) identifier.
    """
    toolset = _get_toolset(api_key)
    if toolset is None:
        return {"error": "Composio SDK not available or not configured"}

    try:
        entity = toolset.get_entity(id=entity_id)
        connection_request = entity.initiate_connection(
            app_name=app_name,
            redirect_url=redirect_url,
        )
        return {
            "url": getattr(connection_request, "redirectUrl", getattr(connection_request, "redirect_url", "")),
            "connection_id": getattr(
                connection_request, "connectedAccountId", getattr(connection_request, "connection_id", "")
            ),
            "status": "pending",
        }
    except Exception as exc:
        logger.error("composio_oauth_initiate_failed", app=app_name, error=str(exc))
        return {"error": f"OAuth initiation failed: {exc}"}


def get_composio_connection_status(
    app_name: str,
    *,
    api_key: str | None = None,
    entity_id: str = "default",
) -> dict[str, Any]:
    """Check whether a user has connected a Composio app.

    Returns a dict with:
        - connected: bool
        - app: str
        - status: str  ("active", "pending", "disconnected", "unknown")
    """
    toolset = _get_toolset(api_key)
    if toolset is None:
        return {"connected": False, "app": app_name, "status": "sdk_unavailable"}

    try:
        entity = toolset.get_entity(id=entity_id)
        connections = entity.get_connections()

        for conn in connections:
            conn_app = getattr(conn, "appName", getattr(conn, "app_name", ""))
            if conn_app.lower() == app_name.lower():
                conn_status = getattr(conn, "status", "unknown")
                return {
                    "connected": conn_status == "active",
                    "app": app_name,
                    "status": conn_status,
                }

        return {"connected": False, "app": app_name, "status": "not_connected"}
    except Exception as exc:
        logger.error("composio_status_check_failed", app=app_name, error=str(exc))
        return {"connected": False, "app": app_name, "status": "error", "error": str(exc)}
