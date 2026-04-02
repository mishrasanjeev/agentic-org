"""Slack connector — real Slack Web API integration."""

from __future__ import annotations

from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class SlackConnector(BaseConnector):
    name = "slack"
    category = "comms"
    auth_type = "bolt_bot_token"
    base_url = "https://slack.com/api"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["send_message"] = self.send_message
        self._tool_registry["create_channel"] = self.create_channel
        self._tool_registry["upload_file"] = self.upload_file
        self._tool_registry["search_messages"] = self.search_messages
        self._tool_registry["list_channels"] = self.list_channels
        self._tool_registry["set_reminder"] = self.set_reminder
        self._tool_registry["post_alert"] = self.post_alert

    async def _authenticate(self):
        bot_token = self._get_secret("bot_token")
        if not bot_token:
            bot_token = self._get_secret("access_token")
        self._auth_headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def _slack_post(self, path: str, data: dict | None = None) -> dict[str, Any]:
        """POST to Slack Web API and handle the ok/error envelope."""
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.post(path, json=data)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            error = body.get("error", "unknown_error")
            logger.warning("slack_api_error", path=path, error=error)
            raise RuntimeError(f"Slack API error: {error}")
        return body

    async def _slack_get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """GET from Slack Web API and handle the ok/error envelope."""
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            error = body.get("error", "unknown_error")
            logger.warning("slack_api_error", path=path, error=error)
            raise RuntimeError(f"Slack API error: {error}")
        return body

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await self._slack_post("/auth.test")
            return {
                "status": "healthy",
                "team": data.get("team"),
                "user": data.get("user"),
                "bot_id": data.get("bot_id"),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Send Message ───────────────────────────────────────────────────

    async def send_message(self, **params) -> dict[str, Any]:
        """Send a message to a Slack channel or thread.

        Required params:
            channel: Channel ID (e.g. "C01234ABCDE")
            text: Message text (used as fallback if blocks are provided)
        Optional params:
            blocks: List of Block Kit block objects for rich formatting
            thread_ts: Thread timestamp to reply in a thread
            unfurl_links: Whether to unfurl URLs (default true)
        """
        channel = params.get("channel", "")
        text = params.get("text", "")
        if not channel:
            return {"error": "channel is required"}
        if not text and not params.get("blocks"):
            return {"error": "text or blocks is required"}

        body: dict[str, Any] = {
            "channel": channel,
            "text": text,
        }
        if params.get("blocks"):
            body["blocks"] = params["blocks"]
        if params.get("thread_ts"):
            body["thread_ts"] = params["thread_ts"]
        if "unfurl_links" in params:
            body["unfurl_links"] = params["unfurl_links"]

        data = await self._slack_post("/chat.postMessage", body)
        return {
            "channel": data.get("channel"),
            "ts": data.get("ts"),
            "message": data.get("message", {}),
        }

    # ── Create Channel ─────────────────────────────────────────────────

    async def create_channel(self, **params) -> dict[str, Any]:
        """Create a new Slack channel.

        Required params:
            name: Channel name (lowercase, no spaces, max 80 chars)
        Optional params:
            is_private: Whether to create as private channel (default false)
        """
        name = params.get("name", "")
        if not name:
            return {"error": "name is required"}

        body: dict[str, Any] = {"name": name}
        if params.get("is_private"):
            body["is_private"] = True

        data = await self._slack_post("/conversations.create", body)
        channel_info = data.get("channel", {})
        return {
            "channel_id": channel_info.get("id"),
            "name": channel_info.get("name"),
            "is_private": channel_info.get("is_private", False),
        }

    # ── Upload File ────────────────────────────────────────────────────

    async def upload_file(self, **params) -> dict[str, Any]:
        """Upload a file to a Slack channel.

        Required params:
            channel_id: Target channel ID
            filename: Name for the file
            content: Text content of the file
        Optional params:
            title: Display title for the file
            initial_comment: Message to post with the file
        """
        channel_id = params.get("channel_id", "")
        filename = params.get("filename", "")
        content = params.get("content", "")
        if not channel_id or not filename:
            return {"error": "channel_id and filename are required"}

        body: dict[str, Any] = {
            "channel_id": channel_id,
            "filename": filename,
            "content": content,
        }
        if params.get("title"):
            body["title"] = params["title"]
        if params.get("initial_comment"):
            body["initial_comment"] = params["initial_comment"]

        data = await self._slack_post("/files.uploadV2", body)
        return {
            "file_id": data.get("file", {}).get("id"),
            "url": data.get("file", {}).get("url_private"),
        }

    # ── Search Messages ────────────────────────────────────────────────

    async def search_messages(self, **params) -> dict[str, Any]:
        """Search messages across the workspace.

        Required params:
            query: Search query string
        Optional params:
            sort: Sort order — "score" (relevance) or "timestamp" (default "score")
            count: Number of results (default 20, max 100)
        """
        query = params.get("query", "")
        if not query:
            return {"error": "query is required"}

        query_params: dict[str, Any] = {"query": query}
        if params.get("sort"):
            query_params["sort"] = params["sort"]
        query_params["count"] = params.get("count", 20)

        data = await self._slack_get("/search.messages", query_params)
        messages_data = data.get("messages", {})
        return {
            "total": messages_data.get("total", 0),
            "matches": [
                {
                    "text": m.get("text", ""),
                    "user": m.get("user", ""),
                    "channel": m.get("channel", {}).get("name", ""),
                    "ts": m.get("ts", ""),
                    "permalink": m.get("permalink", ""),
                }
                for m in messages_data.get("matches", [])
            ],
        }

    # ── List Channels ──────────────────────────────────────────────────

    async def list_channels(self, **params) -> dict[str, Any]:
        """List channels in the workspace.

        Optional params:
            types: Comma-separated channel types
                   (default "public_channel,private_channel")
            limit: Max results (default 100, max 1000)
            cursor: Pagination cursor from previous response
        """
        query_params: dict[str, Any] = {
            "types": params.get("types", "public_channel,private_channel"),
            "limit": params.get("limit", 100),
        }
        if params.get("cursor"):
            query_params["cursor"] = params["cursor"]

        data = await self._slack_get("/conversations.list", query_params)
        return {
            "channels": [
                {
                    "id": ch.get("id"),
                    "name": ch.get("name"),
                    "is_private": ch.get("is_private", False),
                    "num_members": ch.get("num_members", 0),
                    "topic": ch.get("topic", {}).get("value", ""),
                }
                for ch in data.get("channels", [])
            ],
            "next_cursor": data.get("response_metadata", {}).get("next_cursor", ""),
        }

    # ── Set Reminder ───────────────────────────────────────────────────

    async def set_reminder(self, **params) -> dict[str, Any]:
        """Set a reminder for a user.

        Required params:
            text: Reminder text
            time: When to remind — Unix timestamp or natural language
                  (e.g. "in 15 minutes", "tomorrow at 9am")
        Optional params:
            user: User ID to remind (defaults to authed user)
        """
        text = params.get("text", "")
        time = params.get("time", "")
        if not text or not time:
            return {"error": "text and time are required"}

        body: dict[str, Any] = {
            "text": text,
            "time": time,
        }
        if params.get("user"):
            body["user"] = params["user"]

        data = await self._slack_post("/reminders.add", body)
        reminder = data.get("reminder", {})
        return {
            "reminder_id": reminder.get("id"),
            "text": reminder.get("text"),
            "complete_ts": reminder.get("complete_ts"),
        }

    # ── Post Alert ─────────────────────────────────────────────────────

    async def post_alert(self, **params) -> dict[str, Any]:
        """Post a formatted alert card to a Slack channel using Block Kit.

        Required params:
            channel: Channel ID
            title: Alert title
            message: Alert body text
        Optional params:
            severity: "info", "warning", "error", "critical" (changes emoji)
            fields: dict of key-value pairs to show in the alert card
            thread_ts: Thread timestamp to reply in a thread
        """
        channel = params.get("channel", "")
        title = params.get("title", "")
        message = params.get("message", "")
        if not channel or not title:
            return {"error": "channel and title are required"}

        severity = params.get("severity", "info")
        emoji_map = {
            "info": ":information_source:",
            "warning": ":warning:",
            "error": ":x:",
            "critical": ":rotating_light:",
        }
        emoji = emoji_map.get(severity, ":information_source:")

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{emoji} {title}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message},
            },
        ]

        fields = params.get("fields", {})
        if fields:
            field_elements = [
                {"type": "mrkdwn", "text": f"*{k}:*\n{v}"}
                for k, v in fields.items()
            ]
            blocks.append({"type": "section", "fields": field_elements})

        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Severity: *{severity.upper()}* | Sent by AgenticOrg",
                    }
                ],
            }
        )

        body: dict[str, Any] = {
            "channel": channel,
            "text": f"[{severity.upper()}] {title}: {message}",
            "blocks": blocks,
        }
        if params.get("thread_ts"):
            body["thread_ts"] = params["thread_ts"]

        data = await self._slack_post("/chat.postMessage", body)
        return {
            "channel": data.get("channel"),
            "ts": data.get("ts"),
            "message": data.get("message", {}),
        }
