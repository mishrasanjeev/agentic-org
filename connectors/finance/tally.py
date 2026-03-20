"""Tally connector — finance."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class TallyConnector(BaseConnector):
    name = "tally"
    category = "finance"
    auth_type = "tdl_rest"
    base_url = "http://localhost:9000/tally"
    rate_limit_rpm = 60

    def _register_tools(self):
    self._tool_registry["post_voucher"] = self.post_voucher
    self._tool_registry["get_ledger_balance"] = self.get_ledger_balance
    self._tool_registry["generate_gst_report"] = self.generate_gst_report
    self._tool_registry["export_tally_xml_data"] = self.export_tally_xml_data
    self._tool_registry["get_trial_balance"] = self.get_trial_balance
    self._tool_registry["get_stock_summary"] = self.get_stock_summary

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

async def post_voucher(self, **params):
    """Execute post_voucher on tally."""
    return await self._post("/post/voucher", params)


async def get_ledger_balance(self, **params):
    """Execute get_ledger_balance on tally."""
    return await self._post("/get/ledger/balance", params)


async def generate_gst_report(self, **params):
    """Execute generate_gst_report on tally."""
    return await self._post("/generate/gst/report", params)


async def export_tally_xml_data(self, **params):
    """Execute export_tally_xml_data on tally."""
    return await self._post("/export/tally/xml/data", params)


async def get_trial_balance(self, **params):
    """Execute get_trial_balance on tally."""
    return await self._post("/get/trial/balance", params)


async def get_stock_summary(self, **params):
    """Execute get_stock_summary on tally."""
    return await self._post("/get/stock/summary", params)

