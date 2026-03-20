"""Zoom connector — hr."""
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

async def create_meeting(self, **params):
    """Execute create_meeting on zoom."""
    return await self._post("/create/meeting", params)


async def get_recording(self, **params):
    """Execute get_recording on zoom."""
    return await self._post("/get/recording", params)


async def cancel_meeting(self, **params):
    """Execute cancel_meeting on zoom."""
    return await self._post("/cancel/meeting", params)


async def get_attendance_report(self, **params):
    """Execute get_attendance_report on zoom."""
    return await self._post("/get/attendance/report", params)


async def add_panelist(self, **params):
    """Execute add_panelist on zoom."""
    return await self._post("/add/panelist", params)


async def get_transcript(self, **params):
    """Execute get_transcript on zoom."""
    return await self._post("/get/transcript", params)

