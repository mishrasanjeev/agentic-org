"""Gstn connector — finance."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class GstnConnector(BaseConnector):
    name = "gstn"
    category = "finance"
    auth_type = "gsp_dsc"
    base_url = "https://gsp.adaequare.com/gsp/authenticate"
    rate_limit_rpm = 50

    def _register_tools(self):
        self._tool_registry["fetch_gstr2a"] = self.fetch_gstr2a
        self._tool_registry["push_gstr1_data"] = self.push_gstr1_data
        self._tool_registry["file_gstr3b"] = self.file_gstr3b
        self._tool_registry["file_gstr9"] = self.file_gstr9
        self._tool_registry["generate_eway_bill"] = self.generate_eway_bill
        self._tool_registry["generate_einvoice_irn"] = self.generate_einvoice_irn
        self._tool_registry["check_filing_status"] = self.check_filing_status
        self._tool_registry["get_compliance_notice"] = self.get_compliance_notice

    async def _authenticate(self):
        dsc_path = self._get_secret("dsc_path")
        api_key = self._get_secret("api_key")
        self._auth_headers = {"X-API-Key": api_key, "X-DSC-Path": dsc_path}

    async def fetch_gstr2a(self, **params):
        """Execute fetch_gstr2a on gstn."""
        return await self._post("/fetch/gstr2a", params)


    async def push_gstr1_data(self, **params):
        """Execute push_gstr1_data on gstn."""
        return await self._post("/push/gstr1/data", params)


    async def file_gstr3b(self, **params):
        """Execute file_gstr3b on gstn."""
        return await self._post("/file/gstr3b", params)


    async def file_gstr9(self, **params):
        """Execute file_gstr9 on gstn."""
        return await self._post("/file/gstr9", params)


    async def generate_eway_bill(self, **params):
        """Execute generate_eway_bill on gstn."""
        return await self._post("/generate/eway/bill", params)


    async def generate_einvoice_irn(self, **params):
        """Execute generate_einvoice_irn on gstn."""
        return await self._post("/generate/einvoice/irn", params)


    async def check_filing_status(self, **params):
        """Execute check_filing_status on gstn."""
        return await self._post("/check/filing/status", params)


    async def get_compliance_notice(self, **params):
        """Execute get_compliance_notice on gstn."""
        return await self._post("/get/compliance/notice", params)

