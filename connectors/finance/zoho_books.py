"""Zoho Books connector — finance."""
from __future__ import annotations
from typing import Any
import httpx
from connectors.framework.base_connector import BaseConnector

class ZohoBooksConnector(BaseConnector):
    name = "zoho_books"
    category = "finance"
    auth_type = "oauth2"
    base_url = "https://books.zoho.in/api/v3"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["create_invoice"] = self.create_invoice
        self._tool_registry["record_expense"] = self.record_expense
        self._tool_registry["reconcile_bank_statement"] = self.reconcile_bank_statement
        self._tool_registry["generate_financial_report"] = self.generate_financial_report
        self._tool_registry["get_balance_sheet"] = self.get_balance_sheet
        self._tool_registry["manage_chart_of_accounts"] = self.manage_chart_of_accounts

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

    async def create_invoice(self, **params):
        """Execute create_invoice on zoho_books."""
        return await self._post("/create/invoice", params)


    async def record_expense(self, **params):
        """Execute record_expense on zoho_books."""
        return await self._post("/record/expense", params)


    async def reconcile_bank_statement(self, **params):
        """Execute reconcile_bank_statement on zoho_books."""
        return await self._post("/reconcile/bank/statement", params)


    async def generate_financial_report(self, **params):
        """Execute generate_financial_report on zoho_books."""
        return await self._post("/generate/financial/report", params)


    async def get_balance_sheet(self, **params):
        """Execute get_balance_sheet on zoho_books."""
        return await self._post("/get/balance/sheet", params)


    async def manage_chart_of_accounts(self, **params):
        """Execute manage_chart_of_accounts on zoho_books."""
        return await self._post("/manage/chart/of/accounts", params)

