"""Microsoft Teams connector — Bot Framework SDK with Composio fallback.

Uses the Bot Framework SDK (botbuilder-core) when available for native
Teams integration. Falls back to Composio Teams actions when the SDK
is not installed, ensuring the connector works in all environments.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector
from connectors.registry import ConnectorRegistry

logger = structlog.get_logger()

_TEAMS_BACKEND_UNAVAILABLE = "No Teams execution backend available"
_COMPOSIO_TOOL_NOT_EXECUTED = (
    "Composio Teams tool was discovered, but this connector has no configured "
    "Composio execution adapter for the action"
)

# Guard import — botbuilder-core is optional
_HAS_BOT_SDK = False
try:
    from botbuilder.core import TurnContext  # type: ignore[import-untyped,import-not-found]

    _HAS_BOT_SDK = True
except ImportError:
    TurnContext = None  # type: ignore[assignment,misc]


def _get_composio_teams_action(action_name: str) -> Any | None:
    """Retrieve a Composio Teams action by name, or None if unavailable."""
    try:
        from connectors.composio.discovery import discover_composio_tools

        tools = discover_composio_tools()
        for tool in tools:
            if tool.get("app", "").lower() == "microsoft_teams" and action_name in tool.get("tool_name", ""):
                return tool
    except ImportError:
        pass
    return None


def _teams_backend_error(
    *,
    operation: str,
    composio_action: Any | None,
    **context: Any,
) -> dict[str, Any]:
    """Return an explicit failure for unavailable Teams execution backends."""
    if composio_action:
        tool_name = composio_action.get("tool_name", "")
        logger.warning(
            "teams_composio_action_not_executed",
            operation=operation,
            composio_tool=tool_name,
            **context,
        )
        return {
            "status": "error",
            "error": _COMPOSIO_TOOL_NOT_EXECUTED,
            "error_code": "teams_composio_action_not_executed",
            "retryable": False,
            "composio_tool": tool_name,
            **context,
        }
    return {
        "status": "error",
        "error": _TEAMS_BACKEND_UNAVAILABLE,
        "error_code": "teams_backend_unavailable",
        "retryable": False,
        **context,
    }


class TeamsConnector(BaseConnector):
    """Microsoft Teams connector with Bot Framework SDK + Composio fallback."""

    name = "microsoft_teams"
    category = "comms"
    auth_type = "bot_framework"
    base_url = "https://smba.trafficmanager.net/teams"
    rate_limit_rpm = 60

    def _register_tools(self) -> None:
        self._tool_registry["send_message"] = self.send_message
        self._tool_registry["list_channels"] = self.list_channels
        self._tool_registry["respond_to_mention"] = self.respond_to_mention

    async def _authenticate(self) -> None:
        """Authenticate using Bot Framework app credentials."""
        app_id = self._get_secret("microsoft_app_id")
        app_password = self._get_secret("microsoft_app_password")
        if app_id and app_password:
            self._auth_headers = {
                "Authorization": f"Bearer {app_id}",
                "Content-Type": "application/json",
            }
        else:
            logger.debug("teams_auth_no_credentials_using_composio_fallback")

    # ── Send Message ───────────────────────────────────────────────────

    async def send_message(self, **params: Any) -> dict[str, Any]:
        """Send a message to a Teams channel or chat.

        Params:
            channel_id: The Teams channel ID.
            message: The message text (supports Adaptive Cards JSON).
            conversation_id: Optional conversation/thread ID.
        """
        channel_id = params.get("channel_id", "")
        message = params.get("message", "")
        conversation_id = params.get("conversation_id", "")

        if _HAS_BOT_SDK and self._client:
            # Native Bot Framework SDK path
            payload = {
                "type": "message",
                "text": message,
                "channelId": channel_id,
            }
            if conversation_id:
                payload["conversation"] = {"id": conversation_id}

            try:
                result = await self._post(
                    f"/v3/conversations/{conversation_id or channel_id}/activities",
                    data=payload,
                )
                return {"status": "sent", "activity_id": result.get("id", ""), "channel_id": channel_id}
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                logger.warning("teams_send_sdk_failed_trying_composio", error=str(exc))

        # Composio fallback
        composio_action = _get_composio_teams_action("send_message")
        return _teams_backend_error(
            operation="send_message",
            composio_action=composio_action,
            channel_id=channel_id,
            message_length=len(message),
        )

    # ── List Channels ─────────────────────────────────────────────────

    async def list_channels(self, **params: Any) -> dict[str, Any]:
        """List channels in a Teams team.

        Params:
            team_id: The Teams team ID.
        """
        team_id = params.get("team_id", "")

        if _HAS_BOT_SDK and self._client:
            try:
                result = await self._get(f"/v3/teams/{team_id}/conversations")
                channels = result.get("conversations", [])
                return {"status": "ok", "team_id": team_id, "channels": channels}
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                logger.warning("teams_list_channels_sdk_failed", error=str(exc))

        # Composio fallback
        composio_action = _get_composio_teams_action("list_channels")
        return _teams_backend_error(
            operation="list_channels",
            composio_action=composio_action,
            team_id=team_id,
        )

    # ── Respond to Mention ────────────────────────────────────────────

    async def respond_to_mention(self, **params: Any) -> dict[str, Any]:
        """Respond to an @mention of the bot in Teams.

        Params:
            activity_id: The activity ID of the mention.
            conversation_id: The conversation/thread ID.
            response_text: The response message.
        """
        activity_id = params.get("activity_id", "")
        conversation_id = params.get("conversation_id", "")
        response_text = params.get("response_text", "")

        if _HAS_BOT_SDK and self._client:
            payload = {
                "type": "message",
                "text": response_text,
                "replyToId": activity_id,
            }
            try:
                result = await self._post(
                    f"/v3/conversations/{conversation_id}/activities",
                    data=payload,
                )
                return {
                    "status": "replied",
                    "activity_id": result.get("id", ""),
                    "in_reply_to": activity_id,
                }
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                logger.warning("teams_respond_sdk_failed", error=str(exc))

        # Composio fallback
        composio_action = _get_composio_teams_action("respond_to_mention")
        return _teams_backend_error(
            operation="respond_to_mention",
            composio_action=composio_action,
            in_reply_to=activity_id,
        )


# Auto-register on import
ConnectorRegistry.register(TeamsConnector)
