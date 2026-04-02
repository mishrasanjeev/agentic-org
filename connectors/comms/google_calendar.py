"""Google Calendar connector — comms.

Integrates with Google Calendar API v3 for event management,
availability checking, and meeting room booking.
"""

from __future__ import annotations

from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector


class GoogleCalendarConnector(BaseConnector):
    name = "google_calendar"
    category = "comms"
    auth_type = "oauth2"
    base_url = "https://www.googleapis.com/calendar/v3"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["create_event"] = self.create_event
        self._tool_registry["list_events"] = self.list_events
        self._tool_registry["check_availability"] = self.check_availability
        self._tool_registry["delete_event"] = self.delete_event
        self._tool_registry["find_free_slot"] = self.find_free_slot

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        refresh_token = self._get_secret("refresh_token")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]

        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get("/calendars/primary")
            return {"status": "healthy", "calendar": result.get("summary", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def create_event(self, **params) -> dict[str, Any]:
        """Create a calendar event.

        Params: summary (required), start (dict: {dateTime, timeZone}),
                end (dict: {dateTime, timeZone}), attendees (list of {email}),
                description, location, conferenceDataVersion (1 for Google Meet).
        """
        calendar_id = params.pop("calendar_id", "primary")
        return await self._post(f"/calendars/{calendar_id}/events", params)

    async def list_events(self, **params) -> dict[str, Any]:
        """List upcoming events.

        Params: calendar_id (default "primary"), timeMin (ISO8601), timeMax,
                maxResults (default 50), q (search query), singleEvents (true/false).
        """
        calendar_id = params.pop("calendar_id", "primary")
        params.setdefault("maxResults", 50)
        params.setdefault("singleEvents", True)
        params.setdefault("orderBy", "startTime")
        return await self._get(f"/calendars/{calendar_id}/events", params)

    async def check_availability(self, **params) -> dict[str, Any]:
        """Check free/busy status for calendars.

        Params: timeMin (ISO8601, required), timeMax (required),
                calendar_ids (list of email addresses to check).
        """
        items = [{"id": cid} for cid in params.get("calendar_ids", ["primary"])]
        return await self._post("/freeBusy", {
            "timeMin": params["timeMin"],
            "timeMax": params["timeMax"],
            "items": items,
        })

    async def delete_event(self, **params) -> dict[str, Any]:
        """Delete/cancel a calendar event.

        Params: event_id (required), calendar_id (default "primary"),
                sendUpdates (all/externalOnly/none).
        """
        calendar_id = params.get("calendar_id", "primary")
        event_id = params["event_id"]
        query = {}
        if params.get("sendUpdates"):
            query["sendUpdates"] = params["sendUpdates"]
        return await self._delete(f"/calendars/{calendar_id}/events/{event_id}")

    async def find_free_slot(self, **params) -> dict[str, Any]:
        """Find a free slot across multiple attendees.

        Params: attendees (list of email addresses), duration_minutes (required),
                timeMin (ISO8601), timeMax, timezone.
        """
        attendees = params.get("attendees", [])
        free_busy = await self.check_availability(
            timeMin=params["timeMin"],
            timeMax=params["timeMax"],
            calendar_ids=attendees,
        )
        return {
            "free_busy": free_busy,
            "duration_minutes": params.get("duration_minutes", 30),
            "attendees": attendees,
        }
