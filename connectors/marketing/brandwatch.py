"""Brandwatch connector — marketing.

Integrates with Brandwatch Consumer Research API for brand monitoring,
sentiment analysis, and share of voice tracking.
"""

from __future__ import annotations

from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector


class BrandwatchConnector(BaseConnector):
    name = "brandwatch"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://api.brandwatch.com"
    rate_limit_rpm = 60

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._project_id = self.config.get("project_id", "")

    def _register_tools(self):
        self._tool_registry["get_mentions"] = self.get_mentions
        self._tool_registry["get_mention_summary"] = self.get_mention_summary
        self._tool_registry["get_share_of_voice"] = self.get_share_of_voice
        self._tool_registry["create_alert"] = self.create_alert
        self._tool_registry["export_report"] = self.export_report

    async def _authenticate(self):
        username = self._get_secret("username")
        password = self._get_secret("password")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/oauth/token",
                data={
                    "grant_type": "api-password",
                    "username": username,
                    "password": password,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]

        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get("/me")
            return {"status": "healthy", "username": result.get("username", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def get_mentions(self, **params) -> dict[str, Any]:
        """Get brand mentions with filters.

        Params: query_id (required), startDate (ISO8601), endDate,
                pageSize (default 50), page, sentiment (optional: positive/negative/neutral).
        """
        return await self._get(f"/projects/{self._project_id}/data/mentions", params)

    async def get_mention_summary(self, **params) -> dict[str, Any]:
        """Get aggregated mention summary (volume, sentiment, topics).

        Params: query_id (required), startDate, endDate.
        """
        return await self._get(f"/projects/{self._project_id}/data/mentions/summary", params)

    async def get_share_of_voice(self, **params) -> dict[str, Any]:
        """Get share of voice comparison across queries/brands.

        Params: query_ids (list of query IDs), startDate, endDate.
        """
        return await self._get(f"/projects/{self._project_id}/data/share-of-voice", params)

    async def create_alert(self, **params) -> dict[str, Any]:
        """Create a volume spike or sentiment alert.

        Params: name (required), query_id (required), type (volume_spike/sentiment_drop),
                threshold (int), notification_email.
        """
        return await self._post(f"/projects/{self._project_id}/alerts", params)

    async def export_report(self, **params) -> dict[str, Any]:
        """Export a mention report as CSV/Excel.

        Params: query_id (required), startDate, endDate, format (csv/xlsx).
        """
        return await self._post(f"/projects/{self._project_id}/data/export", params)
