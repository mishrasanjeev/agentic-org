"""Sanctions API connector — ops / compliance.

Integrates with sanctions.io API v2 for KYC/AML screening —
entity name screening, transaction party checks, and batch screening.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class SanctionsApiConnector(BaseConnector):
    name = "sanctions_api"
    category = "ops"
    auth_type = "api_key"
    base_url = "https://api.sanctions.io/v2"
    rate_limit_rpm = 500

    def _register_tools(self):
        self._tool_registry["screen_entity"] = self.screen_entity
        self._tool_registry["screen_transaction"] = self.screen_transaction
        self._tool_registry["get_alert"] = self.get_alert
        self._tool_registry["batch_screen"] = self.batch_screen
        self._tool_registry["generate_report"] = self.generate_report

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._post("/search", {"name": "test"})
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def screen_entity(self, **params) -> dict[str, Any]:
        """Screen an entity name against sanctions lists.

        Params: name (required), type (individual/entity, default individual),
                date_of_birth (optional YYYY-MM-DD), nationality (optional 2-letter),
                min_score (0-100, default 80).
        """
        params.setdefault("min_score", 80)
        return await self._post("/search", params)

    async def screen_transaction(self, **params) -> dict[str, Any]:
        """Screen all parties in a transaction.

        Params: sender_name (required), receiver_name (required),
                sender_country (optional), receiver_country (optional),
                amount (optional), currency (optional).
        """
        return await self._post("/search/transaction", params)

    async def get_alert(self, **params) -> dict[str, Any]:
        """Get details of a screening alert.

        Params: alert_id (required).
        """
        alert_id = params["alert_id"]
        return await self._get(f"/alerts/{alert_id}")

    async def batch_screen(self, **params) -> dict[str, Any]:
        """Screen multiple entities in a single batch.

        Params: entities (list of {name, type, date_of_birth, nationality}),
                min_score (default 80).
        """
        return await self._post("/search/batch", params)

    async def generate_report(self, **params) -> dict[str, Any]:
        """Generate a screening report for audit purposes.

        Params: screening_id (required), format (pdf/json, default json).
        """
        screening_id = params["screening_id"]
        return await self._get(f"/reports/{screening_id}", {"format": params.get("format", "json")})
