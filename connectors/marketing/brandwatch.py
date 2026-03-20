"""Brandwatch connector — marketing."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class BrandwatchConnector(BaseConnector):
    name = "brandwatch"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://api.brandwatch.com/projects"
    rate_limit_rpm = 60

    def _register_tools(self):
    self._tool_registry["get_brand_mentions"] = self.get_brand_mentions
    self._tool_registry["analyze_mention_sentiment"] = self.analyze_mention_sentiment
    self._tool_registry["get_share_of_voice"] = self.get_share_of_voice
    self._tool_registry["set_volume_spike_alert"] = self.set_volume_spike_alert
    self._tool_registry["export_mention_report"] = self.export_mention_report

    async def _authenticate(self):
        self._auth_headers = {"Authorization": "Bearer <token>"}

async def get_brand_mentions(self, **params):
    """Execute get_brand_mentions on brandwatch."""
    return await self._post("/get/brand/mentions", params)


async def analyze_mention_sentiment(self, **params):
    """Execute analyze_mention_sentiment on brandwatch."""
    return await self._post("/analyze/mention/sentiment", params)


async def get_share_of_voice(self, **params):
    """Execute get_share_of_voice on brandwatch."""
    return await self._post("/get/share/of/voice", params)


async def set_volume_spike_alert(self, **params):
    """Execute set_volume_spike_alert on brandwatch."""
    return await self._post("/set/volume/spike/alert", params)


async def export_mention_report(self, **params):
    """Execute export_mention_report on brandwatch."""
    return await self._post("/export/mention/report", params)

