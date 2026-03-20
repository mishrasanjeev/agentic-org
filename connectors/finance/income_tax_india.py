"""Income Tax India connector — finance."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class IncomeTaxIndiaConnector(BaseConnector):
    name = "income_tax_india"
    category = "finance"
    auth_type = "dsc"
    base_url = "https://www.incometax.gov.in/iec/foportal/api"
    rate_limit_rpm = 10

    def _register_tools(self):
    self._tool_registry["file_26q_return"] = self.file_26q_return
    self._tool_registry["file_24q_return"] = self.file_24q_return
    self._tool_registry["check_tds_credit_in_26as"] = self.check_tds_credit_in_26as
    self._tool_registry["download_form_16a"] = self.download_form_16a
    self._tool_registry["file_itr"] = self.file_itr
    self._tool_registry["get_compliance_notice"] = self.get_compliance_notice
    self._tool_registry["pay_tax_challan"] = self.pay_tax_challan

    async def _authenticate(self):
        self._auth_headers = {"Authorization": "Bearer <token>"}

async def file_26q_return(self, **params):
    """Execute file_26q_return on income_tax_india."""
    return await self._post("/file/26q/return", params)


async def file_24q_return(self, **params):
    """Execute file_24q_return on income_tax_india."""
    return await self._post("/file/24q/return", params)


async def check_tds_credit_in_26as(self, **params):
    """Execute check_tds_credit_in_26as on income_tax_india."""
    return await self._post("/check/tds/credit/in/26as", params)


async def download_form_16a(self, **params):
    """Execute download_form_16a on income_tax_india."""
    return await self._post("/download/form/16a", params)


async def file_itr(self, **params):
    """Execute file_itr on income_tax_india."""
    return await self._post("/file/itr", params)


async def get_compliance_notice(self, **params):
    """Execute get_compliance_notice on income_tax_india."""
    return await self._post("/get/compliance/notice", params)


async def pay_tax_challan(self, **params):
    """Execute pay_tax_challan on income_tax_india."""
    return await self._post("/pay/tax/challan", params)

