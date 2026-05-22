"""Income Tax India connector — finance."""

from __future__ import annotations

from connectors.framework.base_connector import BaseConnector


def _as_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "n", "missing", "unavailable"}
    return bool(value)


class IncomeTaxIndiaConnector(BaseConnector):
    name = "income_tax_india"
    category = "finance"
    auth_type = "dsc"
    base_url = "https://www.incometax.gov.in/iec/foportal/api"
    rate_limit_rpm = 10

    def _register_tools(self):
        self._tool_registry["calculate_tds"] = self.calculate_tds
        self._tool_registry["map_tds_section"] = self.map_tds_section
        self._tool_registry["validate_pan"] = self.validate_pan
        self._tool_registry["detect_tds_applicability"] = self.detect_tds_applicability
        self._tool_registry["generate_tds_summary"] = self.generate_tds_summary
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
        pan_available = _as_bool(params.get("pan_available"), True)
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

    async def map_tds_section(self, **params):
        """Map a ledger/category/vendor description to the likely TDS section."""
        text = " ".join(
            str(params.get(key) or "")
            for key in (
                "ledger_name",
                "expense_category",
                "description",
                "vendor_type",
                "service_type",
            )
        ).lower()
        rules = [
            ("194J", ("professional", "consulting", "legal", "audit", "technical", "royalty")),
            ("194C", ("contractor", "contract", "labour", "labor", "transport", "works")),
            ("194I", ("rent", "lease", "warehouse", "office premises", "building")),
            ("194H", ("commission", "brokerage")),
            ("194A", ("interest",)),
            ("194O", ("ecommerce", "e-commerce", "marketplace")),
            ("194Q", ("purchase", "goods", "material")),
        ]
        for section, keywords in rules:
            if any(keyword in text for keyword in keywords):
                calc = await self.calculate_tds(
                    amount=params.get("amount") or 1,
                    section=section,
                    deductee_type=params.get("deductee_type"),
                    pan_available=_as_bool(params.get("pan_available"), True),
                )
                return {
                    "section": section,
                    "confidence": 0.8,
                    "reason": f"matched keywords for {section}",
                    "rate": calc.get("rate"),
                }
        return {
            "section": None,
            "confidence": 0.0,
            "reason": "no supported TDS section matched",
        }

    async def validate_pan(self, **params):
        """Validate Indian PAN shape for structured 206AA handling."""
        import re

        pan = str(params.get("pan") or "").strip().upper()
        if not pan:
            return {"valid": False, "pan_available": False, "reason": "PAN missing"}
        valid = re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", pan) is not None
        return {
            "valid": valid,
            "pan": pan,
            "pan_available": valid,
            "reason": "valid PAN format" if valid else "invalid PAN format",
        }

    async def detect_tds_applicability(self, **params):
        """Determine section, PAN availability, and calculation for one transaction."""
        section = str(params.get("section") or "").upper().replace("SECTION", "").replace(" ", "")
        if not section:
            mapped = await self.map_tds_section(**params)
            section = mapped.get("section") or ""
        pan_available = params.get("pan_available")
        if pan_available is None and "pan" in params:
            pan_available = (await self.validate_pan(pan=params.get("pan"))).get("valid", False)
        if pan_available is None:
            pan_available = True
        pan_available = _as_bool(pan_available, True)
        if not section:
            return {
                "applicable": False,
                "reason": "section could not be determined",
                "pan_available": bool(pan_available),
            }
        calc = await self.calculate_tds(
            amount=params.get("amount") or params.get("payment_amount"),
            section=section,
            deductee_type=params.get("deductee_type"),
            pan_available=pan_available,
        )
        if calc.get("error"):
            return {"applicable": False, "section": section, "error": calc["error"]}
        return {
            "applicable": True,
            "section": section,
            "pan_available": bool(pan_available),
            "calculation": calc,
        }

    async def generate_tds_summary(self, **params):
        """Generate a section/vendor-wise TDS summary for transaction batches."""
        transactions = params.get("transactions") or []
        if not isinstance(transactions, list):
            return {"error": "transactions must be a list"}
        rows = []
        totals_by_section: dict[str, float] = {}
        total_tds = 0.0
        for index, transaction in enumerate(transactions, start=1):
            if not isinstance(transaction, dict):
                rows.append({"index": index, "error": "transaction must be an object"})
                continue
            result = await self.detect_tds_applicability(**transaction)
            row = {"index": index, **result}
            if result.get("applicable") and isinstance(result.get("calculation"), dict):
                calc = result["calculation"]
                tds_amount = float(calc.get("tds_amount") or 0)
                total_tds += tds_amount
                section = str(calc.get("section") or "")
                totals_by_section[section] = round(totals_by_section.get(section, 0.0) + tds_amount, 2)
            rows.append(row)
        return {
            "status": "summarized",
            "rows": rows,
            "total_tds": round(total_tds, 2),
            "sections": totals_by_section,
            "transaction_count": len(transactions),
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
