"""G2 connector -- marketing / intent data and review intelligence.

Integrates with the G2 API to surface buyer-intent signals, product
reviews, competitor comparisons, and category leadership data for
Account-Based Marketing workflows.
"""

from __future__ import annotations

from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class G2Connector(BaseConnector):
    """G2 buyer-intent and review intelligence connector.

    G2 processes millions of monthly buyer visits.  This connector
    exposes intent signals (which companies are researching your
    category), product reviews, comparison traffic, and category
    leadership rankings.

    Tools:
        get_intent_signals  -- buyer-intent signals for a domain
        get_product_reviews -- reviews for a G2 product listing
        get_comparison_data -- head-to-head comparison data
        get_category_leaders -- leaders grid for a G2 category
    """

    name = "g2"
    category = "marketing"
    auth_type = "api_key"
    base_url = "https://api.g2.com/v1"
    rate_limit_rpm = 100

    # ── Tool registration ──────────────────────────────────────────────

    def _register_tools(self) -> None:
        self._tool_registry["get_intent_signals"] = self.get_intent_signals
        self._tool_registry["get_product_reviews"] = self.get_product_reviews
        self._tool_registry["get_comparison_data"] = self.get_comparison_data
        self._tool_registry["get_category_leaders"] = self.get_category_leaders

    # ── Authentication ─────────────────────────────────────────────────

    async def _authenticate(self) -> None:
        """Set X-Api-Token header from config / GCP Secret Manager."""
        api_key = self._get_secret("api_key")
        if not api_key:
            logger.warning("g2_no_api_key")
        self._auth_headers = {
            "X-Api-Token": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Health check ───────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity by listing a single category."""
        try:
            data = await self._get("/categories", params={"limit": 1})
            return {"status": "healthy", "sample_categories": len(data.get("categories", []))}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}

    # ── Tools ──────────────────────────────────────────────────────────

    async def get_intent_signals(self, **params: Any) -> dict[str, Any]:
        """Get buyer-intent signals for a specific domain.

        Params:
            domain:     company domain to look up
            date_range: optional date range filter (e.g. '7d', '30d', '90d')
        """
        domain = params.get("domain", "")
        if not domain:
            return {"error": "domain parameter is required"}

        query: dict[str, Any] = {"domain": domain}
        date_range = params.get("date_range", "")
        if date_range:
            query["date_range"] = date_range

        data = await self._get("/intent-signals", params=query)
        return {
            "domain": domain,
            "signals": data.get("signals", []),
            "total_signals": data.get("total", 0),
            "intent_score": data.get("intent_score", 0),
        }

    async def get_product_reviews(self, **params: Any) -> dict[str, Any]:
        """Get reviews for a product on G2.

        Params:
            product_slug: G2 product slug (e.g. 'salesforce-crm')
            page:         page number (default 1)
            per_page:     results per page (default 25)
            sort:         sort order (default 'most_recent')
        """
        product_slug = params.get("product_slug", "")
        if not product_slug:
            return {"error": "product_slug parameter is required"}

        query: dict[str, Any] = {
            "page": params.get("page", 1),
            "per_page": params.get("per_page", 25),
            "sort": params.get("sort", "most_recent"),
        }
        data = await self._get(f"/products/{product_slug}/reviews", params=query)
        return {
            "product": product_slug,
            "reviews": data.get("reviews", []),
            "total": data.get("total", 0),
            "average_rating": data.get("average_rating"),
        }

    async def get_comparison_data(self, **params: Any) -> dict[str, Any]:
        """Get head-to-head comparison data between products.

        Params:
            product_ids: comma-separated list of G2 product IDs
        """
        product_ids = params.get("product_ids", "")
        if not product_ids:
            return {"error": "product_ids parameter is required"}

        data = await self._get("/comparisons", params={"product_ids": product_ids})
        return {
            "comparisons": data.get("comparisons", []),
            "products": data.get("products", []),
        }

    async def get_category_leaders(self, **params: Any) -> dict[str, Any]:
        """Get the leaders grid for a G2 category.

        Params:
            category_slug: G2 category slug (e.g. 'crm')
        """
        category_slug = params.get("category_slug", "")
        if not category_slug:
            return {"error": "category_slug parameter is required"}

        data = await self._get(f"/categories/{category_slug}/leaders")
        return {
            "category": category_slug,
            "leaders": data.get("leaders", []),
            "total": data.get("total", 0),
        }
