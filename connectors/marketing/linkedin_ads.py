"""Linkedin Ads connector — marketing."""

from __future__ import annotations

import httpx

from connectors.framework.base_connector import BaseConnector


class LinkedinAdsConnector(BaseConnector):
    name = "linkedin_ads"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://api.linkedin.com/v2"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["create_sponsored_content_campaign"] = (
            self.create_sponsored_content_campaign
        )
        self._tool_registry["get_campaign_impressions"] = self.get_campaign_impressions
        self._tool_registry["create_lead_gen_form"] = self.create_lead_gen_form
        self._tool_registry["define_account_audience_targeting"] = (
            self.define_account_audience_targeting
        )

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        token_url = self.config.get("token_url", f"{self.base_url}/oauth2/token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def create_sponsored_content_campaign(self, **params):
        """Execute create_sponsored_content_campaign on linkedin_ads."""
        return await self._post("/create/sponsored/content/campaign", params)

    async def get_campaign_impressions(self, **params):
        """Execute get_campaign_impressions on linkedin_ads."""
        return await self._post("/get/campaign/impressions", params)

    async def create_lead_gen_form(self, **params):
        """Execute create_lead_gen_form on linkedin_ads."""
        return await self._post("/create/lead/gen/form", params)

    async def define_account_audience_targeting(self, **params):
        """Execute define_account_audience_targeting on linkedin_ads."""
        return await self._post("/define/account/audience/targeting", params)
