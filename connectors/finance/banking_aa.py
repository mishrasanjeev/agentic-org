"""Banking Aa connector — finance."""

from __future__ import annotations

import httpx

from connectors.framework.base_connector import BaseConnector


class BankingAaConnector(BaseConnector):
    name = "banking_aa"
    category = "finance"
    auth_type = "aa_oauth2"
    base_url = "https://aa.finvu.in/api/v1"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["fetch_bank_statement"] = self.fetch_bank_statement
        self._tool_registry["initiate_neft"] = self.initiate_neft
        self._tool_registry["initiate_rtgs"] = self.initiate_rtgs
        self._tool_registry["check_account_balance"] = self.check_account_balance
        self._tool_registry["add_beneficiary"] = self.add_beneficiary
        self._tool_registry["get_transaction_list"] = self.get_transaction_list
        self._tool_registry["cancel_payment"] = self.cancel_payment

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

    async def fetch_bank_statement(self, **params):
        """Execute fetch_bank_statement on banking_aa."""
        return await self._post("/fetch/bank/statement", params)

    async def initiate_neft(self, **params):
        """Execute initiate_neft on banking_aa."""
        return await self._post("/initiate/neft", params)

    async def initiate_rtgs(self, **params):
        """Execute initiate_rtgs on banking_aa."""
        return await self._post("/initiate/rtgs", params)

    async def check_account_balance(self, **params):
        """Execute check_account_balance on banking_aa."""
        return await self._post("/check/account/balance", params)

    async def add_beneficiary(self, **params):
        """Execute add_beneficiary on banking_aa."""
        return await self._post("/add/beneficiary", params)

    async def get_transaction_list(self, **params):
        """Execute get_transaction_list on banking_aa."""
        return await self._post("/get/transaction/list", params)

    async def cancel_payment(self, **params):
        """Execute cancel_payment on banking_aa."""
        return await self._post("/cancel/payment", params)
