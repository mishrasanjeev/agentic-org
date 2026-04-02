"""Greenhouse connector — HR / Recruiting.

Integrates with Greenhouse Harvest API v1 for ATS operations —
job postings, candidate management, interview scheduling, and offers.
"""

from __future__ import annotations

import base64
from typing import Any

from connectors.framework.base_connector import BaseConnector


class GreenhouseConnector(BaseConnector):
    name = "greenhouse"
    category = "hr"
    auth_type = "api_key"
    base_url = "https://harvest.greenhouse.io/v1"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["list_jobs"] = self.list_jobs
        self._tool_registry["get_candidate"] = self.get_candidate
        self._tool_registry["list_applications"] = self.list_applications
        self._tool_registry["schedule_interview"] = self.schedule_interview
        self._tool_registry["create_candidate"] = self.create_candidate
        self._tool_registry["advance_application"] = self.advance_application
        self._tool_registry["reject_application"] = self.reject_application
        self._tool_registry["get_scorecards"] = self.get_scorecards

    async def _authenticate(self):
        # Greenhouse Harvest API uses Basic auth with api_key as username, empty password
        api_key = self._get_secret("api_key")
        credentials = base64.b64encode(f"{api_key}:".encode()).decode()
        self._auth_headers = {"Authorization": f"Basic {credentials}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._get("/jobs", {"per_page": "1"})
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def list_jobs(self, **params) -> dict[str, Any]:
        """List open jobs.

        Params: status (open/closed/draft), department_id, per_page (default 50), page.
        """
        params.setdefault("per_page", "50")
        result = await self._get("/jobs", params)
        return {"jobs": result if isinstance(result, list) else [result]}

    async def get_candidate(self, **params) -> dict[str, Any]:
        """Get candidate details.

        Params: candidate_id (required).
        """
        candidate_id = params["candidate_id"]
        return await self._get(f"/candidates/{candidate_id}")

    async def list_applications(self, **params) -> dict[str, Any]:
        """List applications for a job.

        Params: job_id (optional), status (active/rejected/hired), per_page, page.
        """
        params.setdefault("per_page", "50")
        result = await self._get("/applications", params)
        return {"applications": result if isinstance(result, list) else [result]}

    async def schedule_interview(self, **params) -> dict[str, Any]:
        """Schedule an interview for an application.

        Params: application_id (required), interview_id (required — from interview plan),
                start (ISO8601), end (ISO8601), interviewers (list of {email}).
        """
        app_id = params.pop("application_id")
        return await self._post(f"/applications/{app_id}/scheduled_interviews", params)

    async def create_candidate(self, **params) -> dict[str, Any]:
        """Add a new candidate.

        Params: first_name (required), last_name (required),
                email_addresses (list of {value, type}),
                phone_numbers (optional), applications (list of {job_id}).
        """
        return await self._post("/candidates", params)

    async def advance_application(self, **params) -> dict[str, Any]:
        """Move application to next stage.

        Params: application_id (required), from_stage_id (optional).
        """
        app_id = params.pop("application_id")
        return await self._post(f"/applications/{app_id}/advance", params)

    async def reject_application(self, **params) -> dict[str, Any]:
        """Reject an application.

        Params: application_id (required), rejection_reason_id (optional),
                rejection_email_id (optional), notes (optional).
        """
        app_id = params.pop("application_id")
        return await self._post(f"/applications/{app_id}/reject", params)

    async def get_scorecards(self, **params) -> dict[str, Any]:
        """Get interview scorecards for an application.

        Params: application_id (required).
        """
        app_id = params["application_id"]
        result = await self._get(f"/applications/{app_id}/scorecards")
        return {"scorecards": result if isinstance(result, list) else [result]}
