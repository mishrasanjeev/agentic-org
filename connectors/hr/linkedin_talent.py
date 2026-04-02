"""LinkedIn Talent connector — HR / Recruiting.

Integrates with LinkedIn Recruiter/Talent Solutions API for
job posting, candidate search, and InMail messaging.
Note: Requires LinkedIn Recruiter System Connect (RSC) partnership.
"""

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
        refresh_token = self._get_secret("refresh_token")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]

        self._auth_headers = {
            "Authorization": f"Bearer {token}",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get("/me")
            return {"status": "healthy", "id": result.get("id", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def post_job(self, **params) -> dict[str, Any]:
        """Post a job listing.

        Params: title (required), description (required),
                companyId, location (dict with country, city),
                employmentStatus (FULL_TIME/PART_TIME/CONTRACT),
                experienceLevel (ENTRY_LEVEL/MID_SENIOR_LEVEL/DIRECTOR/EXECUTIVE),
                industries (list), skills (list).
        """
        return await self._post("/simpleJobPostings", params)

    async def search_candidates(self, **params) -> dict[str, Any]:
        """Search for candidates (requires Recruiter license).

        Params: keywords (required), location (optional), company (optional),
                title (optional), start (offset), count (default 25).
        """
        params.setdefault("count", 25)
        return await self._get("/talentSearch", params)

    async def send_inmail(self, **params) -> dict[str, Any]:
        """Send an InMail message to a candidate.

        Params: recipient (URN like urn:li:person:{id}),
                subject (required), body (required).
        """
        return await self._post("/messages", {
            "recipients": [params["recipient"]],
            "subject": params["subject"],
            "body": params["body"],
        })

    async def get_applicants(self, **params) -> dict[str, Any]:
        """Get applicants for a job posting.

        Params: job_id (required), start (offset), count (default 50).
        """
        job_id = params.pop("job_id")
        params.setdefault("count", 50)
        return await self._get(f"/simpleJobPostings/{job_id}/applicants", params)

    async def get_analytics(self, **params) -> dict[str, Any]:
        """Get recruitment analytics for a job posting.

        Params: job_id (required).
        """
        job_id = params["job_id"]
        return await self._get(f"/jobPostingAnalytics/{job_id}")

    async def get_job_insights(self, **params) -> dict[str, Any]:
        """Get market insights for a job title/location.

        Params: title (required), location (optional),
                companyId (optional for competitor comparison).
        """
        return await self._get("/talentInsights", params)
