"""Linkedin Talent connector — hr."""
from __future__ import annotations
from typing import Any
import httpx
from connectors.framework.base_connector import BaseConnector

class LinkedinTalentConnector(BaseConnector):
    name = "linkedin_talent"
    category = "hr"
    auth_type = "oauth2"
    base_url = "https://api.linkedin.com/v2"
    rate_limit_rpm = 50

    def _register_tools(self):
        self._tool_registry["post_job"] = self.post_job
        self._tool_registry["search_candidates"] = self.search_candidates
        self._tool_registry["send_inmail"] = self.send_inmail
        self._tool_registry["get_applicants"] = self.get_applicants
        self._tool_registry["get_analytics"] = self.get_analytics
        self._tool_registry["get_job_insights"] = self.get_job_insights

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

    async def post_job(self, **params):
        """Execute post_job on linkedin_talent."""
        return await self._post("/post/job", params)


    async def search_candidates(self, **params):
        """Execute search_candidates on linkedin_talent."""
        return await self._post("/search/candidates", params)


    async def send_inmail(self, **params):
        """Execute send_inmail on linkedin_talent."""
        return await self._post("/send/inmail", params)


    async def get_applicants(self, **params):
        """Execute get_applicants on linkedin_talent."""
        return await self._post("/get/applicants", params)


    async def get_analytics(self, **params):
        """Execute get_analytics on linkedin_talent."""
        return await self._post("/get/analytics", params)


    async def get_job_insights(self, **params):
        """Execute get_job_insights on linkedin_talent."""
        return await self._post("/get/job/insights", params)

