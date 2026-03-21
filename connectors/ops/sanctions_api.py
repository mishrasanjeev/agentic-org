"""Sanctions Api connector — ops."""
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
        self._tool_registry["screen_entity_name"] = self.screen_entity_name
        self._tool_registry["screen_transaction_parties"] = self.screen_transaction_parties
        self._tool_registry["get_screening_alert"] = self.get_screening_alert
        self._tool_registry["run_batch_screen"] = self.run_batch_screen
        self._tool_registry["generate_screening_report"] = self.generate_screening_report

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

    async def screen_entity_name(self, **params):
        """Execute screen_entity_name on sanctions_api."""
        return await self._post("/screen/entity/name", params)


    async def screen_transaction_parties(self, **params):
        """Execute screen_transaction_parties on sanctions_api."""
        return await self._post("/screen/transaction/parties", params)


    async def get_screening_alert(self, **params):
        """Execute get_screening_alert on sanctions_api."""
        return await self._post("/get/screening/alert", params)


    async def run_batch_screen(self, **params):
        """Execute run_batch_screen on sanctions_api."""
        return await self._post("/run/batch/screen", params)


    async def generate_screening_report(self, **params):
        """Execute generate_screening_report on sanctions_api."""
        return await self._post("/generate/screening/report", params)

