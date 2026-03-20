"""Buffer connector — marketing."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class BufferConnector(BaseConnector):
    name = "buffer"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://api.bufferapp.com/1"
    rate_limit_rpm = 60

    def _register_tools(self):
    self._tool_registry["schedule_social_post"] = self.schedule_social_post
    self._tool_registry["get_post_analytics"] = self.get_post_analytics
    self._tool_registry["manage_publishing_queue"] = self.manage_publishing_queue
    self._tool_registry["approve_draft_post"] = self.approve_draft_post

    async def _authenticate(self):
        self._auth_headers = {"Authorization": "Bearer <token>"}

async def schedule_social_post(self, **params):
    """Execute schedule_social_post on buffer."""
    return await self._post("/schedule/social/post", params)


async def get_post_analytics(self, **params):
    """Execute get_post_analytics on buffer."""
    return await self._post("/get/post/analytics", params)


async def manage_publishing_queue(self, **params):
    """Execute manage_publishing_queue on buffer."""
    return await self._post("/manage/publishing/queue", params)


async def approve_draft_post(self, **params):
    """Execute approve_draft_post on buffer."""
    return await self._post("/approve/draft/post", params)

