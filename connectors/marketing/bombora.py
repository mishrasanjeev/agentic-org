"""Bombora connector -- marketing / intent data.

Integrates with Bombora Company Surge API for B2B intent signals.
Provides topic-level surge scores, weekly reports, and company search
to power Account-Based Marketing workflows.
"""

from __future__ import annotations

from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class BomboraConnector(BaseConnector):
    """Bombora Company Surge intent data connector.

    Surfaces B2B buying intent signals by tracking content consumption
    across 5,000+ B2B websites.  Surge scores indicate when a company
    is researching a topic significantly more than its baseline.

    Tools:
        get_surge_scores   -- surge scores for one or more domains
        get_topic_clusters -- topic clusters a domain is researching
        get_weekly_report  -- aggregate surge report for a date range
        search_companies   -- find companies surging on a topic
    """

    name = "bombora"
    category = "marketing"
    auth_type = "api_key"
    base_url = "https://api.bombora.com/v1"
    rate_limit_rpm = 60

    # ── Tool registration ──────────────────────────────────────────────

    def _register_tools(self) -> None:
        self._tool_registry["get_surge_scores"] = self.get_surge_scores
        self._tool_registry["get_topic_clusters"] = self.get_topic_clusters
        self._tool_registry["get_weekly_report"] = self.get_weekly_report
        self._tool_registry["search_companies"] = self.search_companies

    # ── Authentication ─────────────────────────────────────────────────

    async def _authenticate(self) -> None:
        """Set Bearer token from config / GCP Secret Manager."""
        api_key = self._get_secret("api_key")
        if not api_key:
            logger.warning("bombora_no_api_key")
        self._auth_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Health check ───────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity by fetching a single topic."""
        try:
            data = await self._get("/surge/topics", params={"limit": 1})
            return {"status": "healthy", "sample_topics": len(data.get("topics", []))}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}

    # ── Tools ──────────────────────────────────────────────────────────

    async def get_surge_scores(self, **params: Any) -> dict[str, Any]:
        """Get Company Surge scores for specified domains and topics.

        Params:
            domains:   comma-separated list of company domains
            topic_ids: comma-separated list of Bombora topic IDs (optional)
        """
        domains = params.get("domains", "")
        if not domains:
            return {"error": "domains parameter is required"}

        query: dict[str, Any] = {"domains": domains}
        topic_ids = params.get("topic_ids", "")
        if topic_ids:
            query["topic_ids"] = topic_ids

        data = await self._get("/surge/companies", params=query)
        return {
            "companies": data.get("companies", []),
            "total": data.get("total", 0),
        }

    async def get_topic_clusters(self, **params: Any) -> dict[str, Any]:
        """Get topic clusters that a domain is actively researching.

        Params:
            domain: single company domain
        """
        domain = params.get("domain", "")
        if not domain:
            return {"error": "domain parameter is required"}

        data = await self._get("/surge/topics", params={"domain": domain})
        return {
            "domain": domain,
            "topics": data.get("topics", []),
            "total": data.get("total", 0),
        }

    async def get_weekly_report(self, **params: Any) -> dict[str, Any]:
        """Get an aggregate surge report for a date range.

        Params:
            start_date: ISO date (YYYY-MM-DD)
            end_date:   ISO date (YYYY-MM-DD)
        """
        start_date = params.get("start_date", "")
        end_date = params.get("end_date", "")
        if not start_date or not end_date:
            return {"error": "start_date and end_date are required"}

        data = await self._get(
            "/surge/report",
            params={"start_date": start_date, "end_date": end_date},
        )
        return {
            "period": {"start": start_date, "end": end_date},
            "report": data.get("report", data),
        }

    async def search_companies(self, **params: Any) -> dict[str, Any]:
        """Search for companies surging on a specific topic.

        Params:
            topic_id:  Bombora topic ID
            min_score: minimum surge score (default 60)
            limit:     max results (default 50)
        """
        topic_id = params.get("topic_id", "")
        if not topic_id:
            return {"error": "topic_id parameter is required"}

        query: dict[str, Any] = {
            "topic_id": topic_id,
            "min_score": params.get("min_score", 60),
            "limit": params.get("limit", 50),
        }
        data = await self._get("/surge/companies/search", params=query)
        return {
            "topic_id": topic_id,
            "companies": data.get("companies", []),
            "total": data.get("total", 0),
        }
