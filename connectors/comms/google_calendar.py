"""Google Calendar connector — comms."""
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
    self._tool_registry["create_calendar_event"] = self.create_calendar_event
    self._tool_registry["check_participant_availability"] = self.check_participant_availability
    self._tool_registry["book_meeting_room"] = self.book_meeting_room
    self._tool_registry["cancel_event"] = self.cancel_event
    self._tool_registry["find_optimal_meeting_slot"] = self.find_optimal_meeting_slot

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

async def create_calendar_event(self, **params):
    """Execute create_calendar_event on google_calendar."""
    return await self._post("/create/calendar/event", params)


async def check_participant_availability(self, **params):
    """Execute check_participant_availability on google_calendar."""
    return await self._post("/check/participant/availability", params)


async def book_meeting_room(self, **params):
    """Execute book_meeting_room on google_calendar."""
    return await self._post("/book/meeting/room", params)


async def cancel_event(self, **params):
    """Execute cancel_event on google_calendar."""
    return await self._post("/cancel/event", params)


async def find_optimal_meeting_slot(self, **params):
    """Execute find_optimal_meeting_slot on google_calendar."""
    return await self._post("/find/optimal/meeting/slot", params)

