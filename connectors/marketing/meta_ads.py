"""Meta Ads connector — marketing.

Integrates with Meta Marketing API v21.0 (Facebook/Instagram Ads).
Uses the Graph API pattern: GET/POST /{object_id}?fields=...
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class MetaAdsConnector(BaseConnector):
    name = "meta_ads"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://graph.facebook.com/v21.0"
    rate_limit_rpm = 200

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._ad_account_id = self.config.get("ad_account_id", "")

    def _register_tools(self):
        self._tool_registry["get_campaign_insights"] = self.get_campaign_insights
        self._tool_registry["update_campaign_budget"] = self.update_campaign_budget
        self._tool_registry["create_custom_audience"] = self.create_custom_audience
        self._tool_registry["update_adset_status"] = self.update_adset_status
        self._tool_registry["get_ad_account_info"] = self.get_ad_account_info

    async def _authenticate(self):
        access_token = self._get_secret("access_token")
        self._auth_headers = {"Authorization": f"Bearer {access_token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get("/me", {"fields": "id,name"})
            return {"status": "healthy", "name": result.get("name", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def get_campaign_insights(self, **params) -> dict[str, Any]:
        """Get campaign performance insights.

        Params: time_range (dict with since/until YYYY-MM-DD),
                campaign_id (optional — all campaigns if omitted),
                fields (optional, default: impressions,clicks,spend,actions).
        """
        fields = params.get(
            "fields",
            "campaign_name,impressions,clicks,spend,actions,cost_per_action_type,ctr,cpc",
        )
        time_range = params.get("time_range", {})
        query: dict[str, Any] = {"fields": fields}
        if time_range:
            query["time_range"] = time_range

        if params.get("campaign_id"):
            return await self._get(f"/{params['campaign_id']}/insights", query)
        return await self._get(f"/act_{self._ad_account_id}/insights", query)

    async def update_campaign_budget(self, **params) -> dict[str, Any]:
        """Update a campaign's daily or lifetime budget.

        Params: campaign_id (required), daily_budget (in cents) or
                lifetime_budget (in cents).
        """
        campaign_id = params.pop("campaign_id")
        return await self._post(f"/{campaign_id}", params)

    async def create_custom_audience(self, **params) -> dict[str, Any]:
        """Create a Custom Audience for targeting.

        Params: name (required), description, subtype (CUSTOM, LOOKALIKE, WEBSITE),
                customer_file_source (optional for CUSTOM type).
        """
        return await self._post(f"/act_{self._ad_account_id}/customaudiences", params)

    async def update_adset_status(self, **params) -> dict[str, Any]:
        """Pause or activate an ad set.

        Params: adset_id (required), status (ACTIVE or PAUSED).
        """
        adset_id = params.pop("adset_id")
        return await self._post(f"/{adset_id}", {"status": params.get("status", "PAUSED")})

    async def get_ad_account_info(self, **params) -> dict[str, Any]:
        """Get ad account info including spend limits and balance.

        Params: fields (optional).
        """
        fields = params.get(
            "fields",
            "name,account_status,amount_spent,balance,currency,spend_cap",
        )
        return await self._get(f"/act_{self._ad_account_id}", {"fields": fields})
