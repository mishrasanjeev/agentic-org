"""Zoom connector — HR / Meetings.

Integrates with Zoom REST API v2 for meeting management,
recording access, attendance reports, and transcript retrieval.
"""

from __future__ import annotations

from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector


class ZoomConnector(BaseConnector):
    name = "zoom"
    category = "hr"
    auth_type = "oauth2"
    base_url = "https://api.zoom.us/v2"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["create_meeting"] = self.create_meeting
        self._tool_registry["get_recording"] = self.get_recording
        self._tool_registry["cancel_meeting"] = self.cancel_meeting
        self._tool_registry["get_attendance_report"] = self.get_attendance_report
        self._tool_registry["add_panelist"] = self.add_panelist
        self._tool_registry["get_transcript"] = self.get_transcript

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        account_id = self._get_secret("account_id")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://zoom.us/oauth/token",
                params={"grant_type": "account_credentials", "account_id": account_id},
                auth=(client_id, client_secret),
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]

        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get("/users/me")
            return {"status": "healthy", "email": result.get("email", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def create_meeting(self, **params) -> dict[str, Any]:
        """Create a Zoom meeting.

        Params: topic (required), type (1=instant, 2=scheduled),
                start_time (ISO8601 for scheduled), duration (minutes),
                timezone, agenda, user_id (default "me").
        """
        user_id = params.pop("user_id", "me")
        return await self._post(f"/users/{user_id}/meetings", params)

    async def get_recording(self, **params) -> dict[str, Any]:
        """Get cloud recordings for a meeting.

        Params: meeting_id (required).
        """
        meeting_id = params["meeting_id"]
        return await self._get(f"/meetings/{meeting_id}/recordings")

    async def cancel_meeting(self, **params) -> dict[str, Any]:
        """Cancel/delete a scheduled meeting.

        Params: meeting_id (required).
        """
        meeting_id = params["meeting_id"]
        return await self._delete(f"/meetings/{meeting_id}")

    async def get_attendance_report(self, **params) -> dict[str, Any]:
        """Get participant attendance for a past meeting.

        Params: meeting_id (required), page_size (default 30).
        """
        meeting_id = params.pop("meeting_id")
        params.setdefault("page_size", 30)
        return await self._get(f"/past_meetings/{meeting_id}/participants", params)

    async def add_panelist(self, **params) -> dict[str, Any]:
        """Add panelists to a webinar.

        Params: webinar_id (required), panelists (list of {name, email}).
        """
        webinar_id = params.pop("webinar_id")
        return await self._post(f"/webinars/{webinar_id}/panelists", {"panelists": params.get("panelists", [])})

    async def get_transcript(self, **params) -> dict[str, Any]:
        """Get meeting transcript (if audio transcript is enabled).

        Params: meeting_id (required).
        """
        meeting_id = params["meeting_id"]
        recordings = await self._get(f"/meetings/{meeting_id}/recordings")
        transcript_files = [
            f for f in recordings.get("recording_files", [])
            if f.get("file_type") == "TRANSCRIPT"
        ]
        return {"meeting_id": meeting_id, "transcripts": transcript_files}
