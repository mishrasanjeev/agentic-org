"""MoEngage connector — marketing automation.

Integrates with MoEngage API for campaign management, push notifications,
user segmentation, and engagement analytics. India-focused marketing
automation platform widely used by Indian enterprises.
"""

from __future__ import annotations

import base64
from typing import Any

from connectors.framework.base_connector import BaseConnector


class MoEngageConnector(BaseConnector):
    name = "moengage"
    category = "marketing"
    auth_type = "basic"
    base_url = "https://api-01.moengage.com/v1"
    rate_limit_rpm = 200

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        # MoEngage datacenter: api-01, api-02, api-03, etc.
        datacenter = self.config.get("datacenter", "01")
        if "base_url" not in self.config:
            self.base_url = f"https://api-{datacenter}.moengage.com/v1"

    def _register_tools(self):
        self._tool_registry["create_campaign"] = self.create_campaign
        self._tool_registry["get_campaign_stats"] = self.get_campaign_stats
        self._tool_registry["create_segment"] = self.create_segment
        self._tool_registry["send_push_notification"] = self.send_push_notification
        self._tool_registry["get_user_profile"] = self.get_user_profile
        self._tool_registry["track_event"] = self.track_event

    async def _authenticate(self):
        app_id = self._get_secret("app_id")
        api_key = self._get_secret("api_key")
        # MoEngage uses Basic auth with app_id:api_key
        credentials = base64.b64encode(f"{app_id}:{api_key}".encode()).decode()
        self._auth_headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "MOE-APPKEY": app_id,
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            # MoEngage doesn't have a dedicated health endpoint;
            # use campaign list with limit=1 as a lightweight check
            await self._get("/campaigns", {"limit": 1})
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def create_campaign(self, **params) -> dict[str, Any]:
        """Create a marketing campaign.

        Params: name (required), type (push/email/sms/in_app),
                segment_id (optional), content (dict with title, message, etc.),
                schedule (dict with type: immediate/scheduled, time: ISO8601).
        """
        return await self._post("/campaigns/create", params)

    async def get_campaign_stats(self, **params) -> dict[str, Any]:
        """Get campaign performance statistics.

        Params: campaign_id (required), start_date (YYYY-MM-DD), end_date.
        """
        campaign_id = params.pop("campaign_id")
        return await self._get(f"/campaigns/{campaign_id}/stats", params)

    async def create_segment(self, **params) -> dict[str, Any]:
        """Create a user segment for targeting.

        Params: name (required), description, filters (list of filter conditions),
                operator (AND/OR for combining filters).
        """
        return await self._post("/segments/create", params)

    async def send_push_notification(self, **params) -> dict[str, Any]:
        """Send a push notification to a segment or individual users.

        Params: title (required), message (required),
                segment_id or user_ids (list),
                platform (android/ios/web), deep_link (optional).
        """
        return await self._post("/push/send", params)

    async def get_user_profile(self, **params) -> dict[str, Any]:
        """Get a user's MoEngage profile.

        Params: user_id (required) — the MoEngage customer ID.
        """
        user_id = params["user_id"]
        return await self._get(f"/users/{user_id}/profile")

    async def track_event(self, **params) -> dict[str, Any]:
        """Track a custom event for a user.

        Params: user_id (required), event_name (required),
                attributes (optional dict of event properties),
                timestamp (optional ISO8601).
        """
        return await self._post("/events/track", params)
