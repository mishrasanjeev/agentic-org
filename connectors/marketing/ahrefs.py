"""Ahrefs connector — marketing."""

from __future__ import annotations

from connectors.framework.base_connector import BaseConnector


class AhrefsConnector(BaseConnector):
    name = "ahrefs"
    category = "marketing"
    auth_type = "api_token"
    base_url = "https://api.ahrefs.com/v3"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["get_keyword_ranking_history"] = self.get_keyword_ranking_history
        self._tool_registry["identify_content_gaps_vs_competitor"] = (
            self.identify_content_gaps_vs_competitor
        )
        self._tool_registry["get_backlink_profile"] = self.get_backlink_profile
        self._tool_registry["get_domain_rating"] = self.get_domain_rating
        self._tool_registry["export_crawl_issues"] = self.export_crawl_issues

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

    async def get_keyword_ranking_history(self, **params):
        """Execute get_keyword_ranking_history on ahrefs."""
        return await self._post("/get/keyword/ranking/history", params)

    async def identify_content_gaps_vs_competitor(self, **params):
        """Execute identify_content_gaps_vs_competitor on ahrefs."""
        return await self._post("/identify/content/gaps/vs/competitor", params)

    async def get_backlink_profile(self, **params):
        """Execute get_backlink_profile on ahrefs."""
        return await self._post("/get/backlink/profile", params)

    async def get_domain_rating(self, **params):
        """Execute get_domain_rating on ahrefs."""
        return await self._post("/get/domain/rating", params)

    async def export_crawl_issues(self, **params):
        """Execute export_crawl_issues on ahrefs."""
        return await self._post("/export/crawl/issues", params)
