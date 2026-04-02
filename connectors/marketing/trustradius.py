"""TrustRadius connector -- marketing / intent data and review intelligence.

Integrates with the TrustRadius API to surface buyer-intent activity,
product reviews, comparison traffic, and vendor search for Account-Based
Marketing workflows.
"""

from __future__ import annotations

from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class TrustRadiusConnector(BaseConnector):
    """TrustRadius buyer-intent and review intelligence connector.

    TrustRadius tracks buyer research activity across product pages,
    comparisons, and review content.  This connector exposes intent
    signals, reviews, comparison traffic, and vendor search.

    Tools:
        get_buyer_intent      -- buyer activity / intent for a vendor
        get_product_reviews   -- reviews for a specific product
        get_comparison_traffic -- comparison page traffic data
        search_vendors        -- search the TrustRadius vendor catalog
    """

    name = "trustradius"
    category = "marketing"
    auth_type = "api_key"
    base_url = "https://api.trustradius.com/v1"
    rate_limit_rpm = 60

    # ── Tool registration ──────────────────────────────────────────────

    def _register_tools(self) -> None:
        self._tool_registry["get_buyer_intent"] = self.get_buyer_intent
        self._tool_registry["get_product_reviews"] = self.get_product_reviews
        self._tool_registry["get_comparison_traffic"] = self.get_comparison_traffic
        self._tool_registry["search_vendors"] = self.search_vendors

    # ── Authentication ─────────────────────────────────────────────────

    async def _authenticate(self) -> None:
        """Set Bearer token from config / GCP Secret Manager."""
        api_key = self._get_secret("api_key")
        if not api_key:
            logger.warning("trustradius_no_api_key")
        self._auth_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Health check ───────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity by performing a minimal vendor search."""
        try:
            data = await self._get(
                "/vendors/search",
                params={"q": "test", "limit": 1},
            )
            return {"status": "healthy", "sample_vendors": len(data.get("vendors", []))}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}

    # ── Tools ──────────────────────────────────────────────────────────

    async def get_buyer_intent(self, **params: Any) -> dict[str, Any]:
        """Get buyer activity / intent data for a vendor.

        Params:
            vendor_id:  TrustRadius vendor ID
            start_date: ISO date (YYYY-MM-DD)
            end_date:   ISO date (YYYY-MM-DD)
        """
        vendor_id = params.get("vendor_id", "")
        if not vendor_id:
            return {"error": "vendor_id parameter is required"}

        query: dict[str, Any] = {"vendor_id": vendor_id}
        start_date = params.get("start_date", "")
        end_date = params.get("end_date", "")
        if start_date:
            query["start_date"] = start_date
        if end_date:
            query["end_date"] = end_date

        data = await self._get("/intent/buyer-activity", params=query)
        return {
            "vendor_id": vendor_id,
            "activity": data.get("activity", []),
            "intent_score": data.get("intent_score", 0),
            "total_views": data.get("total_views", 0),
        }

    async def get_product_reviews(self, **params: Any) -> dict[str, Any]:
        """Get reviews for a product on TrustRadius.

        Params:
            product_id: TrustRadius product ID
            page:       page number (default 1)
            limit:      results per page (default 25)
            sort:       sort order (default 'recent')
        """
        product_id = params.get("product_id", "")
        if not product_id:
            return {"error": "product_id parameter is required"}

        query: dict[str, Any] = {
            "page": params.get("page", 1),
            "limit": params.get("limit", 25),
            "sort": params.get("sort", "recent"),
        }
        data = await self._get(f"/products/{product_id}/reviews", params=query)
        return {
            "product_id": product_id,
            "reviews": data.get("reviews", []),
            "total": data.get("total", 0),
            "average_rating": data.get("average_rating"),
        }

    async def get_comparison_traffic(self, **params: Any) -> dict[str, Any]:
        """Get comparison page traffic data for a product.

        Params:
            product_id: TrustRadius product ID
            date_range: optional date range (e.g. '7d', '30d', '90d')
        """
        product_id = params.get("product_id", "")
        if not product_id:
            return {"error": "product_id parameter is required"}

        query: dict[str, Any] = {"product_id": product_id}
        date_range = params.get("date_range", "")
        if date_range:
            query["date_range"] = date_range

        data = await self._get("/intent/comparisons", params=query)
        return {
            "product_id": product_id,
            "comparisons": data.get("comparisons", []),
            "total_traffic": data.get("total_traffic", 0),
        }

    async def search_vendors(self, **params: Any) -> dict[str, Any]:
        """Search the TrustRadius vendor catalog.

        Params:
            query:    search term
            category: optional category filter
        """
        q = params.get("query", "")
        if not q:
            return {"error": "query parameter is required"}

        search_params: dict[str, Any] = {"q": q}
        category = params.get("category", "")
        if category:
            search_params["category"] = category

        data = await self._get("/vendors/search", params=search_params)
        return {
            "query": q,
            "vendors": data.get("vendors", []),
            "total": data.get("total", 0),
        }
