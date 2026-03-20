"""Mixpanel connector — marketing."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class MixpanelConnector(BaseConnector):
    name = "mixpanel"
    category = "marketing"
    auth_type = "api_key"
    base_url = "https://mixpanel.com/api/2.0"
    rate_limit_rpm = 100

    def _register_tools(self):
    self._tool_registry["get_funnel_conversion_data"] = self.get_funnel_conversion_data
    self._tool_registry["get_retention_cohort"] = self.get_retention_cohort
    self._tool_registry["query_event_data"] = self.query_event_data
    self._tool_registry["create_user_cohort"] = self.create_user_cohort
    self._tool_registry["export_raw_event_data"] = self.export_raw_event_data

    async def _authenticate(self):
        self._auth_headers = {"Authorization": "Bearer <token>"}

async def get_funnel_conversion_data(self, **params):
    """Execute get_funnel_conversion_data on mixpanel."""
    return await self._post("/get/funnel/conversion/data", params)


async def get_retention_cohort(self, **params):
    """Execute get_retention_cohort on mixpanel."""
    return await self._post("/get/retention/cohort", params)


async def query_event_data(self, **params):
    """Execute query_event_data on mixpanel."""
    return await self._post("/query/event/data", params)


async def create_user_cohort(self, **params):
    """Execute create_user_cohort on mixpanel."""
    return await self._post("/create/user/cohort", params)


async def export_raw_event_data(self, **params):
    """Execute export_raw_event_data on mixpanel."""
    return await self._post("/export/raw/event/data", params)

