# ruff: noqa: S608 — GAQL is not SQL; query construction is safe (sent to Google Ads API)
"""Google Ads connector — marketing.

Integrates with Google Ads API v17 via the REST interface.
Google Ads uses GAQL (Google Ads Query Language) through a single
searchStream endpoint rather than individual REST paths per resource.
"""

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

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._customer_id = self.config.get("customer_id", "")
        self._developer_token = self.config.get("developer_token", "")

    def _register_tools(self):
        self._tool_registry["search_campaigns"] = self.search_campaigns
        self._tool_registry["get_campaign_performance"] = self.get_campaign_performance
        self._tool_registry["mutate_campaign_budget"] = self.mutate_campaign_budget
        self._tool_registry["get_search_terms"] = self.get_search_terms
        self._tool_registry["create_user_list"] = self.create_user_list

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

        self._auth_headers = {
            "Authorization": f"Bearer {token}",
            "developer-token": self._developer_token,
        }
        if self.config.get("login_customer_id"):
            self._auth_headers["login-customer-id"] = self.config["login_customer_id"]

    async def _gaql_search(self, query: str) -> list[dict[str, Any]]:
        """Execute a GAQL query via the searchStream endpoint."""
        customer_id = self._customer_id.replace("-", "")
        resp = await self._post(
            f"/customers/{customer_id}/googleAds:searchStream",
            {"query": query},
        )
        if isinstance(resp, list):
            rows: list[dict[str, Any]] = []
            for batch in resp:
                rows.extend(batch.get("results", []))
            return rows
        return resp.get("results", [resp])

    async def health_check(self) -> dict[str, Any]:
        try:
            customer_id = self._customer_id.replace("-", "")
            result = await self._get(f"/customers/{customer_id}")
            return {"status": "healthy", "customer": result.get("resourceName", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def search_campaigns(self, **params) -> dict[str, Any]:
        """Search campaigns with optional status filter.

        Params: status (ENABLED/PAUSED/REMOVED), limit (default 50).
        """
        status_filter = ""
        if params.get("status"):
            status_filter = f" AND campaign.status = '{params['status']}'"
        limit = params.get("limit", 50)
        query = (
            "SELECT campaign.id, campaign.name, campaign.status, "  # noqa: S608  # nosec B608
            "campaign.advertising_channel_type, campaign_budget.amount_micros "
            f"FROM campaign WHERE campaign.status != 'REMOVED'{status_filter} "
            f"LIMIT {limit}"
        )
        rows = await self._gaql_search(query)
        return {"campaigns": rows, "total": len(rows)}

    async def get_campaign_performance(self, **params) -> dict[str, Any]:
        """Get campaign metrics for a date range.

        Params: start_date (YYYY-MM-DD), end_date, campaign_id (optional).
        """
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        cid_filter = ""
        if params.get("campaign_id"):
            cid_filter = f" AND campaign.id = {params['campaign_id']}"
        query = (
            "SELECT campaign.id, campaign.name, "
            "metrics.impressions, metrics.clicks, metrics.cost_micros, "
            "metrics.conversions, metrics.cost_per_conversion "
            f"FROM campaign WHERE segments.date BETWEEN '{start}' AND '{end}'"  # noqa: S608  # nosec B608
            f"{cid_filter}"
        )
        rows = await self._gaql_search(query)
        return {"performance": rows, "date_range": {"start": start, "end": end}}

    async def mutate_campaign_budget(self, **params) -> dict[str, Any]:
        """Update a campaign budget.

        Params: campaign_budget_id (required), amount_micros (required).
        """
        customer_id = self._customer_id.replace("-", "")
        budget_id = params["campaign_budget_id"]
        amount = params["amount_micros"]
        return await self._post(
            f"/customers/{customer_id}/campaignBudgets:mutate",
            {
                "operations": [{
                    "update": {
                        "resourceName": f"customers/{customer_id}/campaignBudgets/{budget_id}",
                        "amountMicros": str(amount),
                    },
                    "updateMask": "amountMicros",
                }],
            },
        )

    async def get_search_terms(self, **params) -> dict[str, Any]:
        """Get search term performance report.

        Params: start_date, end_date, campaign_id (optional), limit (100).
        """
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        limit = params.get("limit", 100)
        cid_filter = ""
        if params.get("campaign_id"):
            cid_filter = f" AND campaign.id = {params['campaign_id']}"
        query = (
            "SELECT search_term_view.search_term, "
            "metrics.impressions, metrics.clicks, metrics.cost_micros, "
            "metrics.conversions "
            f"FROM search_term_view WHERE segments.date BETWEEN '{start}' AND '{end}'"  # noqa: S608  # nosec B608
            f"{cid_filter} ORDER BY metrics.impressions DESC LIMIT {limit}"
        )
        rows = await self._gaql_search(query)
        return {"search_terms": rows, "total": len(rows)}

    async def create_user_list(self, **params) -> dict[str, Any]:
        """Create a remarketing user list.

        Params: name (required), description, membership_life_span_days (30).
        """
        customer_id = self._customer_id.replace("-", "")
        return await self._post(
            f"/customers/{customer_id}/userLists:mutate",
            {
                "operations": [{
                    "create": {
                        "name": params["name"],
                        "description": params.get("description", ""),
                        "membershipLifeSpan": str(params.get("membership_life_span_days", 30)),
                        "crmBasedUserList": {},
                    },
                }],
            },
        )
