"""Ahrefs connector — marketing.

Integrates with Ahrefs API v3 for SEO analytics — domain ratings,
backlink analysis, keyword research, content gaps, and site audits.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class AhrefsConnector(BaseConnector):
    name = "ahrefs"
    category = "marketing"
    auth_type = "api_token"
    base_url = "https://api.ahrefs.com/v3"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["get_domain_rating"] = self.get_domain_rating
        self._tool_registry["get_backlinks"] = self.get_backlinks
        self._tool_registry["get_organic_keywords"] = self.get_organic_keywords
        self._tool_registry["get_content_gap"] = self.get_content_gap
        self._tool_registry["get_site_audit"] = self.get_site_audit

    async def _authenticate(self):
        api_token = self._get_secret("api_token")
        self._auth_headers = {"Authorization": f"Bearer {api_token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._get("/site-explorer/domain-rating", {"target": "ahrefs.com"})
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def get_domain_rating(self, **params) -> dict[str, Any]:
        """Get domain rating and authority metrics.

        Params: target (required — domain like "example.com").
        """
        return await self._get("/site-explorer/domain-rating", params)

    async def get_backlinks(self, **params) -> dict[str, Any]:
        """Get backlink profile for a domain or URL.

        Params: target (required), mode (domain/prefix/exact/subdomains),
                limit (default 50), order_by (ahrefs_rank/domain_rating).
        """
        params.setdefault("mode", "domain")
        params.setdefault("limit", 50)
        return await self._get("/site-explorer/backlinks", params)

    async def get_organic_keywords(self, **params) -> dict[str, Any]:
        """Get organic keyword rankings for a domain.

        Params: target (required), country (2-letter code, default "in"),
                limit (default 50), order_by (volume/position/traffic).
        """
        params.setdefault("country", "in")
        params.setdefault("limit", 50)
        return await self._get("/site-explorer/organic-keywords", params)

    async def get_content_gap(self, **params) -> dict[str, Any]:
        """Find content gaps vs competitors.

        Params: target (required — your domain),
                competitors (required — list of competitor domains),
                country (default "in"), limit (default 50).
        """
        return await self._get("/content-explorer/overview", params)

    async def get_site_audit(self, **params) -> dict[str, Any]:
        """Get site audit crawl issues.

        Params: target (required — domain to audit).
        """
        return await self._get("/site-audit/crawl-issues", params)
