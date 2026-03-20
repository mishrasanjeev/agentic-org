"""Greenhouse connector — hr."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class GreenhouseConnector(BaseConnector):
    name = "greenhouse"
    category = "hr"
    auth_type = "api_key"
    base_url = "https://harvest.greenhouse.io/v1"
    rate_limit_rpm = 100

    def _register_tools(self):
    self._tool_registry["post_job"] = self.post_job
    self._tool_registry["get_applications"] = self.get_applications
    self._tool_registry["move_stage"] = self.move_stage
    self._tool_registry["schedule_interview"] = self.schedule_interview
    self._tool_registry["send_offer"] = self.send_offer
    self._tool_registry["reject_candidate"] = self.reject_candidate
    self._tool_registry["get_scorecard"] = self.get_scorecard
    self._tool_registry["bulk_import_candidates"] = self.bulk_import_candidates

    async def _authenticate(self):
        self._auth_headers = {"Authorization": "Bearer <token>"}

async def post_job(self, **params):
    """Execute post_job on greenhouse."""
    return await self._post("/post/job", params)


async def get_applications(self, **params):
    """Execute get_applications on greenhouse."""
    return await self._post("/get/applications", params)


async def move_stage(self, **params):
    """Execute move_stage on greenhouse."""
    return await self._post("/move/stage", params)


async def schedule_interview(self, **params):
    """Execute schedule_interview on greenhouse."""
    return await self._post("/schedule/interview", params)


async def send_offer(self, **params):
    """Execute send_offer on greenhouse."""
    return await self._post("/send/offer", params)


async def reject_candidate(self, **params):
    """Execute reject_candidate on greenhouse."""
    return await self._post("/reject/candidate", params)


async def get_scorecard(self, **params):
    """Execute get_scorecard on greenhouse."""
    return await self._post("/get/scorecard", params)


async def bulk_import_candidates(self, **params):
    """Execute bulk_import_candidates on greenhouse."""
    return await self._post("/bulk/import/candidates", params)

