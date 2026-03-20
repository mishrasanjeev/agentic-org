"""Pagerduty connector — ops."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class PagerdutyConnector(BaseConnector):
    name = "pagerduty"
    category = "ops"
    auth_type = "api_key"
    base_url = "https://api.pagerduty.com"
    rate_limit_rpm = 100

    def _register_tools(self):
    self._tool_registry["create_incident"] = self.create_incident
    self._tool_registry["trigger_alert_with_context"] = self.trigger_alert_with_context
    self._tool_registry["manage_on_call_schedule"] = self.manage_on_call_schedule
    self._tool_registry["run_automated_runbook"] = self.run_automated_runbook
    self._tool_registry["generate_postmortem_doc"] = self.generate_postmortem_doc
    self._tool_registry["acknowledge_incident"] = self.acknowledge_incident

    async def _authenticate(self):
        self._auth_headers = {"Authorization": "Bearer <token>"}

async def create_incident(self, **params):
    """Execute create_incident on pagerduty."""
    return await self._post("/create/incident", params)


async def trigger_alert_with_context(self, **params):
    """Execute trigger_alert_with_context on pagerduty."""
    return await self._post("/trigger/alert/with/context", params)


async def manage_on_call_schedule(self, **params):
    """Execute manage_on_call_schedule on pagerduty."""
    return await self._post("/manage/on/call/schedule", params)


async def run_automated_runbook(self, **params):
    """Execute run_automated_runbook on pagerduty."""
    return await self._post("/run/automated/runbook", params)


async def generate_postmortem_doc(self, **params):
    """Execute generate_postmortem_doc on pagerduty."""
    return await self._post("/generate/postmortem/doc", params)


async def acknowledge_incident(self, **params):
    """Execute acknowledge_incident on pagerduty."""
    return await self._post("/acknowledge/incident", params)

