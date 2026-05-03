"""Income Tax India connector — finance."""

from __future__ import annotations

from connectors.framework.base_connector import BaseConnector


class IncomeTaxIndiaConnector(BaseConnector):
    name = "income_tax_india"
    category = "finance"
    auth_type = "dsc"
    base_url = "https://www.incometax.gov.in/iec/foportal/api"
    rate_limit_rpm = 10

    def _register_tools(self):
        self._tool_registry["calculate_tds"] = self.calculate_tds
        self._tool_registry["file_form_26q"] = self.file_form_26q
        self._tool_registry["file_26q_return"] = self.file_26q_return
        self._tool_registry["file_24q_return"] = self.file_24q_return
        self._tool_registry["check_tds_credit_in_26as"] = self.check_tds_credit_in_26as
        self._tool_registry["download_form_16a"] = self.download_form_16a
        self._tool_registry["file_itr"] = self.file_itr
        self._tool_registry["get_compliance_notice"] = self.get_compliance_notice
        self._tool_registry["pay_tax_challan"] = self.pay_tax_challan

    async def _authenticate(self):
        dsc_path = self._get_secret("dsc_path")
        api_key = self._get_secret("api_key")
        self._auth_headers = {"X-API-Key": api_key, "X-DSC-Path": dsc_path}

    async def calculate_tds(self, **params):
        """Calculate Indian TDS for a supplied transaction without filing."""
        try:
            amount = float(params.get("amount") or params.get("payment_amount") or 0)
        except (TypeError, ValueError):
            return {"error": "amount must be numeric"}
        if amount <= 0:
            return {"error": "amount is required"}

        section = str(params.get("section") or "").upper().replace("SECTION", "").strip()
        section = section.replace(" ", "")
        deductee_type = str(params.get("deductee_type") or "individual").lower()
        pan_available = bool(params.get("pan_available", True))
        rates = {
            "194A": 0.10,
            "194C": 0.01 if deductee_type in {"individual", "huf"} else 0.02,
            "194H": 0.05,
            "194I": 0.10,
            "194J": 0.10,
            "194O": 0.01,
            "194Q": 0.001,
        }
        if section not in rates:
            return {"error": f"unsupported TDS section: {section or 'missing'}"}

        rate = max(rates[section], 0.20) if not pan_available else rates[section]
        tds_amount = round(amount * rate, 2)
        return {
            "status": "calculated",
            "section": section,
            "amount": amount,
            "rate": rate,
            "tds_amount": tds_amount,
            "net_payable": round(amount - tds_amount, 2),
            "filing_required": True,
        }

    async def file_form_26q(self, **params):
        """Alias for file_26q_return used by TDS chat prompts."""
        return await self.file_26q_return(**params)

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
