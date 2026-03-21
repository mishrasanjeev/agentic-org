"""Mca Portal connector — ops."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class McaPortalConnector(BaseConnector):
    name = "mca_portal"
    category = "ops"
    auth_type = "dsc"
    base_url = "https://www.mca.gov.in/mcafoportal/api"
    rate_limit_rpm = 10

    def _register_tools(self):
        self._tool_registry["file_annual_return"] = self.file_annual_return
        self._tool_registry["complete_director_kyc"] = self.complete_director_kyc
        self._tool_registry["fetch_company_master_data"] = self.fetch_company_master_data
        self._tool_registry["file_charge_satisfaction"] = self.file_charge_satisfaction

    async def _authenticate(self):
        dsc_path = self._get_secret("dsc_path")
        api_key = self._get_secret("api_key")
        self._auth_headers = {"X-API-Key": api_key, "X-DSC-Path": dsc_path}

    async def file_annual_return(self, **params):
        """Execute file_annual_return on mca_portal."""
        return await self._post("/file/annual/return", params)


    async def complete_director_kyc(self, **params):
        """Execute complete_director_kyc on mca_portal."""
        return await self._post("/complete/director/kyc", params)


    async def fetch_company_master_data(self, **params):
        """Execute fetch_company_master_data on mca_portal."""
        return await self._post("/fetch/company/master/data", params)


    async def file_charge_satisfaction(self, **params):
        """Execute file_charge_satisfaction on mca_portal."""
        return await self._post("/file/charge/satisfaction", params)

