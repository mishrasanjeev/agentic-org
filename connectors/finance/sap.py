"""Sap connector — finance."""
from __future__ import annotations
from typing import Any
import httpx
from connectors.framework.base_connector import BaseConnector

class SapConnector(BaseConnector):
    name = "sap"
    category = "finance"
    auth_type = "odata_oauth2"
    base_url = "https://org.s4hana.cloud/sap/opu/odata/sap"
    rate_limit_rpm = 300

    def _register_tools(self):
        self._tool_registry["post_fi_document"] = self.post_fi_document
        self._tool_registry["get_account_balance"] = self.get_account_balance
        self._tool_registry["create_purchase_order"] = self.create_purchase_order
        self._tool_registry["post_goods_receipt"] = self.post_goods_receipt
        self._tool_registry["run_payment_run"] = self.run_payment_run
        self._tool_registry["get_vendor_master"] = self.get_vendor_master
        self._tool_registry["get_cost_center_data"] = self.get_cost_center_data

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

    async def post_fi_document(self, **params):
        """Execute post_fi_document on sap."""
        return await self._post("/post/fi/document", params)


    async def get_account_balance(self, **params):
        """Execute get_account_balance on sap."""
        return await self._post("/get/account/balance", params)


    async def create_purchase_order(self, **params):
        """Execute create_purchase_order on sap."""
        return await self._post("/create/purchase/order", params)


    async def post_goods_receipt(self, **params):
        """Execute post_goods_receipt on sap."""
        return await self._post("/post/goods/receipt", params)


    async def run_payment_run(self, **params):
        """Execute run_payment_run on sap."""
        return await self._post("/run/payment/run", params)


    async def get_vendor_master(self, **params):
        """Execute get_vendor_master on sap."""
        return await self._post("/get/vendor/master", params)


    async def get_cost_center_data(self, **params):
        """Execute get_cost_center_data on sap."""
        return await self._post("/get/cost/center/data", params)

