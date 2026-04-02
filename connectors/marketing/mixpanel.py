"""Mixpanel connector — marketing analytics.

Integrates with Mixpanel API for funnel analysis, retention cohorts,
event querying, and user segmentation. Uses Basic auth with
service account credentials.
"""

from __future__ import annotations

import base64
from typing import Any

from connectors.framework.base_connector import BaseConnector


class MixpanelConnector(BaseConnector):
    name = "mixpanel"
    category = "marketing"
    auth_type = "basic"
    base_url = "https://mixpanel.com/api/2.0"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["get_funnel"] = self.get_funnel
        self._tool_registry["get_retention"] = self.get_retention
        self._tool_registry["query_jql"] = self.query_jql
        self._tool_registry["get_segmentation"] = self.get_segmentation
        self._tool_registry["export_events"] = self.export_events

    async def _authenticate(self):
        # Mixpanel service accounts use Basic auth with username:secret
        username = self._get_secret("service_account_username")
        secret = self._get_secret("service_account_secret")
        credentials = base64.b64encode(f"{username}:{secret}".encode()).decode()
        self._auth_headers = {"Authorization": f"Basic {credentials}"}
        # Project ID needed for some endpoints
        project_id = self.config.get("project_id", "")
        if project_id:
            self._auth_headers["X-Mixpanel-Project-Id"] = project_id

    async def health_check(self) -> dict[str, Any]:
        try:
            # Lightweight check — get a single event count
            await self._get(
                "/events",
                {"event": '["$mp_web_page_view"]', "type": "general", "unit": "day", "interval": 1},
            )
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def get_funnel(self, **params) -> dict[str, Any]:
        """Get funnel conversion data.

        Params: funnel_id (required), from_date (YYYY-MM-DD),
                to_date, unit (day/week/month).
        """
        funnel_id = params.pop("funnel_id")
        return await self._get(f"/funnels/{funnel_id}", params)

    async def get_retention(self, **params) -> dict[str, Any]:
        """Get retention cohort data.

        Params: from_date (YYYY-MM-DD), to_date, born_event (optional),
                retention_type (birth/compounded), unit (day/week/month).
        """
        return await self._get("/retention", params)

    async def query_jql(self, **params) -> dict[str, Any]:
        """Run a JQL (JavaScript Query Language) script.

        Params: script (required — JQL JavaScript string).
        Example: script='function main() { return Events({...}).groupBy(["name"], mixpanel.reducer.count()) }'
        """
        return await self._post("/jql", params)

    async def get_segmentation(self, **params) -> dict[str, Any]:
        """Get event segmentation data.

        Params: event (required — event name), from_date, to_date,
                type (general/unique/average), unit (day/week/month),
                on (optional — property to segment by).
        """
        return await self._get("/segmentation", params)

    async def export_events(self, **params) -> dict[str, Any]:
        """Export raw event data for a date range.

        Params: from_date (YYYY-MM-DD, required), to_date (required),
                event (optional — filter to specific event).
        Note: Uses data export endpoint which is rate-limited.
        """
        # Export uses a different base URL
        if not self._client:
            raise RuntimeError("Connector not connected")
        export_url = "https://data.mixpanel.com/api/2.0/export"
        resp = await self._client.get(export_url, params=params)
        resp.raise_for_status()
        # Export returns newline-delimited JSON
        lines = resp.text.strip().split("\n")
        import json
        events = [json.loads(line) for line in lines if line.strip()]
        return {"events": events, "count": len(events)}
