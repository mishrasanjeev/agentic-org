"""Buffer connector — marketing.

Integrates with Buffer Publish API v1 for social media scheduling,
analytics, and queue management.
"""

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
        self._tool_registry["create_update"] = self.create_update
        self._tool_registry["get_update_analytics"] = self.get_update_analytics
        self._tool_registry["get_pending_updates"] = self.get_pending_updates
        self._tool_registry["list_profiles"] = self.list_profiles
        self._tool_registry["move_to_top"] = self.move_to_top

    async def _authenticate(self):
        access_token = self._get_secret("access_token")
        self._auth_headers = {"Authorization": f"Bearer {access_token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get("/user.json")
            return {"status": "healthy", "name": result.get("name", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def create_update(self, **params) -> dict[str, Any]:
        """Schedule a social media post.

        Params: text (required), profile_ids (list of Buffer profile IDs),
                media (optional dict: link, description, picture),
                scheduled_at (optional ISO8601 — immediate if omitted),
                now (optional bool — post immediately).
        """
        return await self._post("/updates/create.json", params)

    async def get_update_analytics(self, **params) -> dict[str, Any]:
        """Get analytics for a specific post/update.

        Params: update_id (required).
        """
        update_id = params["update_id"]
        return await self._get(f"/updates/{update_id}/interactions.json")

    async def get_pending_updates(self, **params) -> dict[str, Any]:
        """Get pending (queued) updates for a profile.

        Params: profile_id (required), page (optional), count (optional).
        """
        profile_id = params["profile_id"]
        query: dict[str, Any] = {}
        if params.get("page"):
            query["page"] = params["page"]
        if params.get("count"):
            query["count"] = params["count"]
        return await self._get(f"/profiles/{profile_id}/updates/pending.json", query)

    async def list_profiles(self, **params) -> dict[str, Any]:
        """List all connected social media profiles."""
        return await self._get("/profiles.json")

    async def move_to_top(self, **params) -> dict[str, Any]:
        """Move an update to the top of the queue.

        Params: update_id (required).
        """
        update_id = params["update_id"]
        return await self._post(f"/updates/{update_id}/move_to_top.json", {})
