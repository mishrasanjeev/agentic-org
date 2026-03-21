"""Slack connector — comms."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class SlackConnector(BaseConnector):
    name = "slack"
    category = "comms"
    auth_type = "bolt_bot_token"
    base_url = "https://slack.com/api"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["send_message"] = self.send_message
        self._tool_registry["create_channel"] = self.create_channel
        self._tool_registry["post_formatted_alert"] = self.post_formatted_alert
        self._tool_registry["upload_file"] = self.upload_file
        self._tool_registry["set_channel_reminder"] = self.set_channel_reminder
        self._tool_registry["search_message_history"] = self.search_message_history

    async def _authenticate(self):
        bot_token = self._get_secret("bot_token")
        self._auth_headers = {"Authorization": f"Bearer {bot_token}"}

    async def send_message(self, **params):
        """Execute send_message on slack."""
        return await self._post("/send/message", params)


    async def create_channel(self, **params):
        """Execute create_channel on slack."""
        return await self._post("/create/channel", params)


    async def post_formatted_alert(self, **params):
        """Execute post_formatted_alert on slack."""
        return await self._post("/post/formatted/alert", params)


    async def upload_file(self, **params):
        """Execute upload_file on slack."""
        return await self._post("/upload/file", params)


    async def set_channel_reminder(self, **params):
        """Execute set_channel_reminder on slack."""
        return await self._post("/set/channel/reminder", params)


    async def search_message_history(self, **params):
        """Execute search_message_history on slack."""
        return await self._post("/search/message/history", params)

