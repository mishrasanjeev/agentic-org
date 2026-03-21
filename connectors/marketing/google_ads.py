"""Google Ads connector — marketing."""
from __future__ import annotations
from typing import Any
import httpx
from connectors.framework.base_connector import BaseConnector

class GoogleAdsConnector(BaseConnector):
    name = "google_ads"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://googleads.googleapis.com/v17"
    rate_limit_rpm = 200

    def _register_tools(self):
        self._tool_registry["get_campaign_performance_metrics"] = self.get_campaign_performance_metrics
        self._tool_registry["adjust_campaign_budget"] = self.adjust_campaign_budget
        self._tool_registry["pause_underperforming_adset"] = self.pause_underperforming_adset
        self._tool_registry["create_remarketing_audience"] = self.create_remarketing_audience
        self._tool_registry["get_search_term_report"] = self.get_search_term_report

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        token_url = self.config.get("token_url", f"{self.base_url}/oauth2/token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            })
            resp.raise_for_status()
            token = resp.json()["access_token"]
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def get_campaign_performance_metrics(self, **params):
        """Execute get_campaign_performance_metrics on google_ads."""
        return await self._post("/get/campaign/performance/metrics", params)


    async def adjust_campaign_budget(self, **params):
        """Execute adjust_campaign_budget on google_ads."""
        return await self._post("/adjust/campaign/budget", params)


    async def pause_underperforming_adset(self, **params):
        """Execute pause_underperforming_adset on google_ads."""
        return await self._post("/pause/underperforming/adset", params)


    async def create_remarketing_audience(self, **params):
        """Execute create_remarketing_audience on google_ads."""
        return await self._post("/create/remarketing/audience", params)


    async def get_search_term_report(self, **params):
        """Execute get_search_term_report on google_ads."""
        return await self._post("/get/search/term/report", params)

