"""Servicenow connector — ops."""

from __future__ import annotations

import httpx

from connectors.framework.base_connector import BaseConnector


class ServicenowConnector(BaseConnector):
    name = "servicenow"
    category = "ops"
    auth_type = "rest_oauth2"
    base_url = "https://org.service-now.com/api/now"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["create_incident"] = self.create_incident
        self._tool_registry["submit_change_request"] = self.submit_change_request
        self._tool_registry["update_cmdb_ci"] = self.update_cmdb_ci
        self._tool_registry["check_sla_status"] = self.check_sla_status
        self._tool_registry["fulfil_service_catalog_request"] = self.fulfil_service_catalog_request
        self._tool_registry["get_kb_article"] = self.get_kb_article

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        token_url = self.config.get("token_url", f"{self.base_url}/oauth2/token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def create_incident(self, **params):
        """Execute create_incident on servicenow."""
        return await self._post("/create/incident", params)

    async def submit_change_request(self, **params):
        """Execute submit_change_request on servicenow."""
        return await self._post("/submit/change/request", params)

    async def update_cmdb_ci(self, **params):
        """Execute update_cmdb_ci on servicenow."""
        return await self._post("/update/cmdb/ci", params)

    async def check_sla_status(self, **params):
        """Execute check_sla_status on servicenow."""
        return await self._post("/check/sla/status", params)

    async def fulfil_service_catalog_request(self, **params):
        """Execute fulfil_service_catalog_request on servicenow."""
        return await self._post("/fulfil/service/catalog/request", params)

    async def get_kb_article(self, **params):
        """Execute get_kb_article on servicenow."""
        return await self._post("/get/kb/article", params)
