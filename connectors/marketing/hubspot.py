"""Hubspot connector — marketing."""
from __future__ import annotations
from typing import Any
import httpx
from connectors.framework.base_connector import BaseConnector

class HubspotConnector(BaseConnector):
    name = "hubspot"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://api.hubapi.com"
    rate_limit_rpm = 200

    def _register_tools(self):
        self._tool_registry["create_contact"] = self.create_contact
        self._tool_registry["send_marketing_email"] = self.send_marketing_email
        self._tool_registry["create_deal"] = self.create_deal
        self._tool_registry["enrol_in_sequence"] = self.enrol_in_sequence
        self._tool_registry["get_campaign_analytics"] = self.get_campaign_analytics
        self._tool_registry["run_ab_test"] = self.run_ab_test
        self._tool_registry["segment_contact_list"] = self.segment_contact_list

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        token_url = self.config.get("token_url", f"{self.base_url}/oauth2/token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            })
            resp.raise_for_status()
            token = resp.json()["access_token"]
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def create_contact(self, **params):
        """Execute create_contact on hubspot."""
        return await self._post("/create/contact", params)


    async def send_marketing_email(self, **params):
        """Execute send_marketing_email on hubspot."""
        return await self._post("/send/marketing/email", params)


    async def create_deal(self, **params):
        """Execute create_deal on hubspot."""
        return await self._post("/create/deal", params)


    async def enrol_in_sequence(self, **params):
        """Execute enrol_in_sequence on hubspot."""
        return await self._post("/enrol/in/sequence", params)


    async def get_campaign_analytics(self, **params):
        """Execute get_campaign_analytics on hubspot."""
        return await self._post("/get/campaign/analytics", params)


    async def run_ab_test(self, **params):
        """Execute run_ab_test on hubspot."""
        return await self._post("/run/ab/test", params)


    async def segment_contact_list(self, **params):
        """Execute segment_contact_list on hubspot."""
        return await self._post("/segment/contact/list", params)

