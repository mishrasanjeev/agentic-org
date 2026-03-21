"""Stripe connector — finance."""

from __future__ import annotations

from connectors.framework.base_connector import BaseConnector


class StripeConnector(BaseConnector):
    name = "stripe"
    category = "finance"
    auth_type = "api_key"
    base_url = "https://api.stripe.com/v1"
    rate_limit_rpm = 300

    def _register_tools(self):
        self._tool_registry["create_charge"] = self.create_charge
        self._tool_registry["manage_subscription_lifecycle"] = self.manage_subscription_lifecycle
        self._tool_registry["create_payout"] = self.create_payout
        self._tool_registry["get_account_balance"] = self.get_account_balance
        self._tool_registry["manage_dispute"] = self.manage_dispute
        self._tool_registry["generate_financial_report"] = self.generate_financial_report

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

    async def create_charge(self, **params):
        """Execute create_charge on stripe."""
        return await self._post("/create/charge", params)

    async def manage_subscription_lifecycle(self, **params):
        """Execute manage_subscription_lifecycle on stripe."""
        return await self._post("/manage/subscription/lifecycle", params)

    async def create_payout(self, **params):
        """Execute create_payout on stripe."""
        return await self._post("/create/payout", params)

    async def get_account_balance(self, **params):
        """Execute get_account_balance on stripe."""
        return await self._post("/get/account/balance", params)

    async def manage_dispute(self, **params):
        """Execute manage_dispute on stripe."""
        return await self._post("/manage/dispute", params)

    async def generate_financial_report(self, **params):
        """Execute generate_financial_report on stripe."""
        return await self._post("/generate/financial/report", params)
