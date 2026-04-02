"""LinkedIn Ads connector — marketing.

Integrates with LinkedIn Marketing API (REST) for campaign management,
audience targeting, and analytics. Uses the versioned REST API.
Note: Requires LinkedIn Marketing Developer Platform access.
"""

from __future__ import annotations

from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector


class LinkedinAdsConnector(BaseConnector):
    name = "linkedin_ads"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://api.linkedin.com/rest"
    rate_limit_rpm = 100

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._ad_account_id = self.config.get("ad_account_id", "")

    def _register_tools(self):
        self._tool_registry["create_campaign"] = self.create_campaign
        self._tool_registry["get_analytics"] = self.get_analytics
        self._tool_registry["create_lead_gen_form"] = self.create_lead_gen_form
        self._tool_registry["get_targeting_criteria"] = self.get_targeting_criteria

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        refresh_token = self._get_secret("refresh_token")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]

        self._auth_headers = {
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": "202401",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get("/me")
            return {"status": "healthy", "id": result.get("id", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def create_campaign(self, **params) -> dict[str, Any]:
        """Create a sponsored content campaign.

        Params: name (required), account (urn:li:sponsoredAccount:{id}),
                campaignGroup (urn), type (SPONSORED_UPDATES/TEXT_AD/SPONSORED_INMAILS),
                costType (CPM/CPC), dailyBudget (dict with amount, currencyCode),
                status (ACTIVE/PAUSED/DRAFT).
        """
        if not params.get("account"):
            params["account"] = f"urn:li:sponsoredAccount:{self._ad_account_id}"
        return await self._post("/adCampaigns", params)

    async def get_analytics(self, **params) -> dict[str, Any]:
        """Get campaign analytics.

        Params: campaign_id (optional — all if omitted),
                start_date (YYYY-MM-DD), end_date,
                pivot (CAMPAIGN/CREATIVE/COMPANY).
        """
        query: dict[str, str] = {
            "q": "analytics",
            "pivot": params.get("pivot", "CAMPAIGN"),
            "dateRange.start.year": params.get("start_date", "2026-01-01")[:4],
            "dateRange.start.month": str(int(params.get("start_date", "2026-01-01")[5:7])),
            "dateRange.start.day": str(int(params.get("start_date", "2026-01-01")[8:10])),
            "dateRange.end.year": params.get("end_date", "2026-12-31")[:4],
            "dateRange.end.month": str(int(params.get("end_date", "2026-12-31")[5:7])),
            "dateRange.end.day": str(int(params.get("end_date", "2026-12-31")[8:10])),
            "timeGranularity": params.get("granularity", "DAILY"),
        }
        if params.get("campaign_id"):
            query["campaigns"] = f"urn:li:sponsoredCampaign:{params['campaign_id']}"
        return await self._get("/adAnalytics", query)

    async def create_lead_gen_form(self, **params) -> dict[str, Any]:
        """Create a lead generation form.

        Params: name (required), account (urn:li:sponsoredAccount:{id}),
                headline, description, questions (list of field dicts),
                privacyPolicyUrl, thankYouMessage.
        """
        if not params.get("account"):
            params["account"] = f"urn:li:sponsoredAccount:{self._ad_account_id}"
        return await self._post("/leadGenForms", params)

    async def get_targeting_criteria(self, **params) -> dict[str, Any]:
        """Get available targeting criteria for an ad account.

        Params: facet (optional: industries, jobFunctions, seniorities, etc.).
        """
        query: dict[str, str] = {"q": "adAccount", "adAccount": f"urn:li:sponsoredAccount:{self._ad_account_id}"}
        if params.get("facet"):
            query["facet"] = params["facet"]
        return await self._get("/adTargetingEntities", query)
