"""Google Analytics 4 (GA4) connector — marketing.

Integrates with the GA4 Data API v1beta for website analytics,
conversion tracking, and user behavior analysis.
"""

from __future__ import annotations

from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector


class GA4Connector(BaseConnector):
    name = "ga4"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://analyticsdata.googleapis.com/v1beta"
    rate_limit_rpm = 200

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._property_id = self.config.get("property_id", "")

    def _register_tools(self):
        self._tool_registry["run_report"] = self.run_report
        self._tool_registry["run_realtime_report"] = self.run_realtime_report
        self._tool_registry["get_conversions"] = self.get_conversions
        self._tool_registry["get_user_acquisition"] = self.get_user_acquisition
        self._tool_registry["get_page_analytics"] = self.get_page_analytics
        self._tool_registry["get_metadata"] = self.get_metadata

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        refresh_token = self._get_secret("refresh_token")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]

        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get(f"/properties/{self._property_id}/metadata")
            return {"status": "healthy", "dimensions": len(result.get("dimensions", []))}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def run_report(self, **params) -> dict[str, Any]:
        """Run a custom GA4 report.

        Params: dimensions (list of dimension names), metrics (list of metric names),
                start_date (YYYY-MM-DD), end_date, limit (default 100),
                dimension_filter (optional dict), order_bys (optional list).
        """
        body: dict[str, Any] = {
            "dateRanges": [{"startDate": params.get("start_date", "7daysAgo"), "endDate": params.get("end_date", "today")}],
            "dimensions": [{"name": d} for d in params.get("dimensions", ["date"])],
            "metrics": [{"name": m} for m in params.get("metrics", ["sessions", "totalUsers"])],
            "limit": str(params.get("limit", 100)),
        }
        if params.get("dimension_filter"):
            body["dimensionFilter"] = params["dimension_filter"]
        if params.get("order_bys"):
            body["orderBys"] = params["order_bys"]

        return await self._post(f"/properties/{self._property_id}:runReport", body)

    async def run_realtime_report(self, **params) -> dict[str, Any]:
        """Run a realtime report (last 30 minutes of data).

        Params: dimensions (optional, default: country),
                metrics (optional, default: activeUsers).
        """
        body: dict[str, Any] = {
            "dimensions": [{"name": d} for d in params.get("dimensions", ["country"])],
            "metrics": [{"name": m} for m in params.get("metrics", ["activeUsers"])],
        }
        if params.get("limit"):
            body["limit"] = str(params["limit"])

        return await self._post(f"/properties/{self._property_id}:runRealtimeReport", body)

    async def get_conversions(self, **params) -> dict[str, Any]:
        """Get conversion event data for a date range.

        Params: start_date, end_date, event_name (optional — all conversions if omitted).
        """
        dimensions = ["eventName"]
        if params.get("event_name"):
            dimensions.append("date")

        body: dict[str, Any] = {
            "dateRanges": [{"startDate": params.get("start_date", "28daysAgo"), "endDate": params.get("end_date", "today")}],
            "dimensions": [{"name": d} for d in dimensions],
            "metrics": [{"name": "conversions"}, {"name": "eventCount"}, {"name": "totalRevenue"}],
        }
        if params.get("event_name"):
            body["dimensionFilter"] = {
                "filter": {
                    "fieldName": "eventName",
                    "stringFilter": {"value": params["event_name"], "matchType": "EXACT"},
                },
            }

        return await self._post(f"/properties/{self._property_id}:runReport", body)

    async def get_user_acquisition(self, **params) -> dict[str, Any]:
        """Get user acquisition data by source/medium/campaign.

        Params: start_date, end_date, limit (default 50).
        """
        return await self._post(
            f"/properties/{self._property_id}:runReport",
            {
                "dateRanges": [{"startDate": params.get("start_date", "28daysAgo"), "endDate": params.get("end_date", "today")}],
                "dimensions": [{"name": "sessionSource"}, {"name": "sessionMedium"}, {"name": "sessionCampaignName"}],
                "metrics": [{"name": "sessions"}, {"name": "totalUsers"}, {"name": "newUsers"}, {"name": "bounceRate"}, {"name": "averageSessionDuration"}],
                "limit": str(params.get("limit", 50)),
                "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
            },
        )

    async def get_page_analytics(self, **params) -> dict[str, Any]:
        """Get page-level analytics (top pages by views).

        Params: start_date, end_date, limit (default 50).
        """
        return await self._post(
            f"/properties/{self._property_id}:runReport",
            {
                "dateRanges": [{"startDate": params.get("start_date", "28daysAgo"), "endDate": params.get("end_date", "today")}],
                "dimensions": [{"name": "pagePath"}, {"name": "pageTitle"}],
                "metrics": [{"name": "screenPageViews"}, {"name": "totalUsers"}, {"name": "bounceRate"}, {"name": "averageSessionDuration"}],
                "limit": str(params.get("limit", 50)),
                "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
            },
        )

    async def get_metadata(self, **params) -> dict[str, Any]:
        """Get available dimensions and metrics for the property."""
        return await self._get(f"/properties/{self._property_id}/metadata")
