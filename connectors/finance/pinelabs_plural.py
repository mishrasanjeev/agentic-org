"""Pinelabs Plural connector — finance."""
from __future__ import annotations
import base64
from typing import Any
from connectors.framework.base_connector import BaseConnector

class PinelabsPluralConnector(BaseConnector):
    name = "pinelabs_plural"
    category = "finance"
    auth_type = "api_key"
    base_url = "https://api.pluralonline.com/api/v1"
    rate_limit_rpm = 200

    def _register_tools(self):
    self._tool_registry["create_payout"] = self.create_payout
    self._tool_registry["create_payment_link"] = self.create_payment_link
    self._tool_registry["initiate_refund"] = self.initiate_refund
    self._tool_registry["get_settlement_report"] = self.get_settlement_report
    self._tool_registry["manage_subscription"] = self.manage_subscription
    self._tool_registry["get_payout_analytics"] = self.get_payout_analytics

    async def _authenticate(self):
        key_id = self._get_secret("key_id")
        key_secret = self._get_secret("key_secret")
        credentials = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._auth_headers = {"Authorization": f"Basic {credentials}"}

async def create_payout(self, **params):
    """Execute create_payout on pinelabs_plural."""
    return await self._post("/create/payout", params)


async def create_payment_link(self, **params):
    """Execute create_payment_link on pinelabs_plural."""
    return await self._post("/create/payment/link", params)


async def initiate_refund(self, **params):
    """Execute initiate_refund on pinelabs_plural."""
    return await self._post("/initiate/refund", params)


async def get_settlement_report(self, **params):
    """Execute get_settlement_report on pinelabs_plural."""
    return await self._post("/get/settlement/report", params)


async def manage_subscription(self, **params):
    """Execute manage_subscription on pinelabs_plural."""
    return await self._post("/manage/subscription", params)


async def get_payout_analytics(self, **params):
    """Execute get_payout_analytics on pinelabs_plural."""
    return await self._post("/get/payout/analytics", params)

