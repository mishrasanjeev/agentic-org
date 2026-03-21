"""Quickbooks connector — finance."""

from __future__ import annotations

import httpx

from connectors.framework.base_connector import BaseConnector


class QuickbooksConnector(BaseConnector):
    name = "quickbooks"
    category = "finance"
    auth_type = "oauth2"
    base_url = "https://quickbooks.api.intuit.com/v3"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["create_invoice"] = self.create_invoice
        self._tool_registry["record_payment"] = self.record_payment
        self._tool_registry["run_payroll_summary"] = self.run_payroll_summary
        self._tool_registry["generate_financial_report"] = self.generate_financial_report
        self._tool_registry["sync_bank_transactions"] = self.sync_bank_transactions
        self._tool_registry["get_pl_report"] = self.get_pl_report

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        token_url = self.config.get("token_url", f"{self.base_url}/oauth2/token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def create_invoice(self, **params):
        """Execute create_invoice on quickbooks."""
        return await self._post("/create/invoice", params)

    async def record_payment(self, **params):
        """Execute record_payment on quickbooks."""
        return await self._post("/record/payment", params)

    async def run_payroll_summary(self, **params):
        """Execute run_payroll_summary on quickbooks."""
        return await self._post("/run/payroll/summary", params)

    async def generate_financial_report(self, **params):
        """Execute generate_financial_report on quickbooks."""
        return await self._post("/generate/financial/report", params)

    async def sync_bank_transactions(self, **params):
        """Execute sync_bank_transactions on quickbooks."""
        return await self._post("/sync/bank/transactions", params)

    async def get_pl_report(self, **params):
        """Execute get_pl_report on quickbooks."""
        return await self._post("/get/pl/report", params)
