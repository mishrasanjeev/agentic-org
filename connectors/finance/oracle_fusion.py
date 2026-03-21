"""Oracle Fusion connector — finance."""
from __future__ import annotations
import base64
from typing import Any
from connectors.framework.base_connector import BaseConnector

class OracleFusionConnector(BaseConnector):
    name = "oracle_fusion"
    category = "finance"
    auth_type = "rest_soap"
    base_url = "https://org.oraclecloud.com/fscmRestApi/resources"
    rate_limit_rpm = 500

    def _register_tools(self):
        self._tool_registry["post_journal_entry"] = self.post_journal_entry
        self._tool_registry["get_gl_balance"] = self.get_gl_balance
        self._tool_registry["create_ap_invoice"] = self.create_ap_invoice
        self._tool_registry["approve_payment"] = self.approve_payment
        self._tool_registry["get_budget"] = self.get_budget
        self._tool_registry["run_reconciliation"] = self.run_reconciliation
        self._tool_registry["create_po"] = self.create_po
        self._tool_registry["get_cash_flow"] = self.get_cash_flow
        self._tool_registry["run_period_close"] = self.run_period_close
        self._tool_registry["get_trial_balance"] = self.get_trial_balance

    async def _authenticate(self):
        username = self._get_secret("username")
        password = self._get_secret("password")
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._auth_headers = {"Authorization": f"Basic {credentials}"}

    async def post_journal_entry(self, **params):
        """Execute post_journal_entry on oracle_fusion."""
        return await self._post("/post/journal/entry", params)


    async def get_gl_balance(self, **params):
        """Execute get_gl_balance on oracle_fusion."""
        return await self._post("/get/gl/balance", params)


    async def create_ap_invoice(self, **params):
        """Execute create_ap_invoice on oracle_fusion."""
        return await self._post("/create/ap/invoice", params)


    async def approve_payment(self, **params):
        """Execute approve_payment on oracle_fusion."""
        return await self._post("/approve/payment", params)


    async def get_budget(self, **params):
        """Execute get_budget on oracle_fusion."""
        return await self._post("/get/budget", params)


    async def run_reconciliation(self, **params):
        """Execute run_reconciliation on oracle_fusion."""
        return await self._post("/run/reconciliation", params)


    async def create_po(self, **params):
        """Execute create_po on oracle_fusion."""
        return await self._post("/create/po", params)


    async def get_cash_flow(self, **params):
        """Execute get_cash_flow on oracle_fusion."""
        return await self._post("/get/cash/flow", params)


    async def run_period_close(self, **params):
        """Execute run_period_close on oracle_fusion."""
        return await self._post("/run/period/close", params)


    async def get_trial_balance(self, **params):
        """Execute get_trial_balance on oracle_fusion."""
        return await self._post("/get/trial/balance", params)

