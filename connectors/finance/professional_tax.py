"""Professional Tax connector for Indian state PT portals.

The connector deliberately does not hardcode statutory slab rates. PT
rates and exemptions are state-specific and change over time, so callers
must either pass the deducted ``pt_amount`` per employee or provide
effective-date controlled slab rules from the firm's compliance library.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from connectors.framework.base_connector import BaseConnector

MONEY = Decimal("0.01")


PT_STATE_PORTALS: dict[str, dict[str, Any]] = {
    "MH": {
        "state": "Maharashtra",
        "portal_name": "Maharashtra GST Department - Profession Tax",
        "portal_url": "https://mahagst.gov.in/en/profession-tax",
        "supports_online_return": True,
        "supports_challan": True,
        "credential_fields": ["registration_number", "username", "password"],
    },
    "KA": {
        "state": "Karnataka",
        "portal_name": "Karnataka Professional Tax",
        "portal_url": "https://ptax.karnataka.gov.in/",
        "supports_online_return": True,
        "supports_challan": True,
        "credential_fields": ["registration_number", "username", "password"],
    },
    "WB": {
        "state": "West Bengal",
        "portal_name": "West Bengal Profession Tax",
        "portal_url": "https://wbprofessiontax.gov.in/",
        "supports_online_return": True,
        "supports_challan": True,
        "credential_fields": ["registration_number", "username", "password"],
    },
    "TG": {
        "state": "Telangana",
        "portal_name": "Telangana Commercial Taxes",
        "portal_url": "https://tgct.gov.in/tgportal/",
        "supports_online_return": True,
        "supports_challan": True,
        "credential_fields": ["registration_number", "username", "password"],
    },
    "AP": {
        "state": "Andhra Pradesh",
        "portal_name": "Andhra Pradesh Commercial Taxes",
        "portal_url": "https://apct.gov.in/",
        "supports_online_return": True,
        "supports_challan": True,
        "credential_fields": ["registration_number", "username", "password"],
    },
    "GJ": {
        "state": "Gujarat",
        "portal_name": "Gujarat Commercial Tax",
        "portal_url": "https://commercialtax.gujarat.gov.in/",
        "supports_online_return": True,
        "supports_challan": True,
        "credential_fields": ["registration_number", "username", "password"],
    },
    "TN": {
        "state": "Tamil Nadu",
        "portal_name": "Tamil Nadu Commercial Taxes",
        "portal_url": "https://ctd.tn.gov.in/",
        "supports_online_return": True,
        "supports_challan": True,
        "credential_fields": ["registration_number", "username", "password"],
    },
    "KL": {
        "state": "Kerala",
        "portal_name": "Kerala Local Self Government Tax",
        "portal_url": "https://tax.lsgkerala.gov.in/",
        "supports_online_return": True,
        "supports_challan": True,
        "credential_fields": ["registration_number", "username", "password"],
    },
}


def _money(value: Any, *, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a numeric amount") from exc
    if amount < 0:
        raise ValueError(f"{field} cannot be negative")
    return amount


def _state_code(value: str) -> str:
    code = str(value or "").strip().upper()
    if not code:
        raise ValueError("state_code is required")
    if code not in PT_STATE_PORTALS:
        supported = ", ".join(sorted(PT_STATE_PORTALS))
        raise ValueError(f"Unsupported professional tax state_code {code!r}. Supported: {supported}")
    return code


def _find_slab_amount(gross_salary: Decimal, slabs: list[dict[str, Any]]) -> Decimal:
    for slab in slabs:
        min_salary = _money(slab.get("min_salary", "0"), field="slabs.min_salary")
        max_raw = slab.get("max_salary")
        max_salary = _money(max_raw, field="slabs.max_salary") if max_raw not in (None, "") else None
        if gross_salary < min_salary:
            continue
        if max_salary is not None and gross_salary > max_salary:
            continue
        return _money(
            slab.get("monthly_tax", slab.get("amount", "0")),
            field="slabs.monthly_tax",
        )
    raise ValueError(f"No professional tax slab matched salary {gross_salary}")


class ProfessionalTaxConnector(BaseConnector):
    name = "professional_tax"
    category = "finance"
    auth_type = "state_portal"
    base_url = ""
    rate_limit_rpm = 10

    def _register_tools(self) -> None:
        self._tool_registry["list_professional_tax_states"] = self.list_professional_tax_states
        self._tool_registry["validate_professional_tax_registration"] = self.validate_professional_tax_registration
        self._tool_registry["prepare_professional_tax_return"] = self.prepare_professional_tax_return
        self._tool_registry["get_professional_tax_challan_draft"] = self.get_professional_tax_challan_draft
        self._tool_registry["submit_professional_tax_return"] = self.submit_professional_tax_return

    async def _authenticate(self) -> None:
        username = self._get_secret("username")
        password = self._get_secret("password")
        if username and password:
            self._auth_headers = {"X-Portal-User": username}

    async def list_professional_tax_states(self, **_: Any) -> dict[str, Any]:
        return {
            "status": "available",
            "items": [
                {"state_code": code, **meta}
                for code, meta in sorted(PT_STATE_PORTALS.items())
            ],
            "rate_policy": "Pass deducted PT per employee or caller-maintained slabs; rates are not hardcoded.",
        }

    async def validate_professional_tax_registration(self, **params: Any) -> dict[str, Any]:
        state = _state_code(params.get("state_code", ""))
        registration_number = str(params.get("registration_number") or "").strip()
        errors: list[str] = []
        if len(registration_number) < 5:
            errors.append("registration_number is required and must be at least 5 characters")
        portal = PT_STATE_PORTALS[state]
        return {
            "status": "valid" if not errors else "invalid",
            "state_code": state,
            "portal": portal,
            "registration_number": registration_number,
            "errors": errors,
        }

    async def prepare_professional_tax_return(self, **params: Any) -> dict[str, Any]:
        state = _state_code(params.get("state_code", ""))
        filing_period = str(params.get("filing_period") or "").strip()
        registration_number = str(params.get("registration_number") or "").strip()
        employer_name = str(params.get("employer_name") or "").strip()
        employees = params.get("employees") or []
        slabs = params.get("slabs") or []
        interest = _money(params.get("interest", "0"), field="interest")
        penalty = _money(params.get("penalty", "0"), field="penalty")

        if not filing_period:
            raise ValueError("filing_period is required")
        if not registration_number:
            raise ValueError("registration_number is required")
        if not isinstance(employees, list) or not employees:
            raise ValueError("employees must contain at least one payroll row")
        if slabs and not isinstance(slabs, list):
            raise ValueError("slabs must be a list when supplied")

        line_items: list[dict[str, Any]] = []
        gross_wages = Decimal("0.00")
        pt_amount = Decimal("0.00")
        row_errors: list[dict[str, Any]] = []

        for idx, row in enumerate(employees, start=1):
            if not isinstance(row, dict):
                row_errors.append({"row_number": idx, "errors": ["row must be an object"]})
                continue
            employee_ref = str(row.get("employee_id") or row.get("employee_code") or row.get("name") or idx)
            try:
                gross_salary = _money(row.get("gross_salary", row.get("salary", "0")), field="gross_salary")
                if "pt_amount" in row and row.get("pt_amount") not in (None, ""):
                    employee_pt = _money(row.get("pt_amount"), field="pt_amount")
                    source = "payroll_deduction"
                elif slabs:
                    employee_pt = _find_slab_amount(gross_salary, slabs)
                    source = "caller_supplied_slab"
                else:
                    raise ValueError("pt_amount is required when slabs are not supplied")
            except ValueError as exc:
                row_errors.append({"row_number": idx, "employee_ref": employee_ref, "errors": [str(exc)]})
                continue

            gross_wages += gross_salary
            pt_amount += employee_pt
            line_items.append({
                "row_number": idx,
                "employee_ref": employee_ref,
                "employee_name": row.get("employee_name") or row.get("name") or "",
                "gross_salary": str(gross_salary),
                "pt_amount": str(employee_pt),
                "calculation_source": source,
            })

        if row_errors:
            return {
                "status": "invalid",
                "state_code": state,
                "filing_period": filing_period,
                "summary": {
                    "input_rows": len(employees),
                    "accepted_rows": len(line_items),
                    "failed_rows": len(row_errors),
                },
                "row_errors": row_errors,
            }

        total_payable = (pt_amount + interest + penalty).quantize(MONEY)
        payload = {
            "state_code": state,
            "portal": PT_STATE_PORTALS[state],
            "registration_number": registration_number,
            "employer_name": employer_name,
            "filing_period": filing_period,
            "employee_count": len(line_items),
            "gross_wages": str(gross_wages.quantize(MONEY)),
            "pt_amount": str(pt_amount.quantize(MONEY)),
            "interest": str(interest),
            "penalty": str(penalty),
            "total_payable": str(total_payable),
            "line_items": line_items,
        }
        return {
            "status": "ready_for_filing",
            "payload": payload,
            "challan_draft": {
                "registration_number": registration_number,
                "state_code": state,
                "filing_period": filing_period,
                "amount": str(total_payable),
                "breakup": {
                    "tax": str(pt_amount.quantize(MONEY)),
                    "interest": str(interest),
                    "penalty": str(penalty),
                },
            },
        }

    async def get_professional_tax_challan_draft(self, **params: Any) -> dict[str, Any]:
        prepared = await self.prepare_professional_tax_return(**params)
        if prepared["status"] != "ready_for_filing":
            return prepared
        return {
            "status": "ready",
            "state_code": prepared["payload"]["state_code"],
            "challan_draft": prepared["challan_draft"],
        }

    async def submit_professional_tax_return(self, **params: Any) -> dict[str, Any]:
        state = _state_code(params.get("state_code", ""))
        payload = params.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        if params.get("dry_run", True):
            return {
                "status": "ready_for_manual_upload",
                "state_code": state,
                "portal": PT_STATE_PORTALS[state],
                "payload": payload,
                "reason": "dry_run enabled; no state portal mutation performed",
            }
        if not self._has_credentials():
            return {
                "status": "not_connected",
                "state_code": state,
                "portal": PT_STATE_PORTALS[state],
                "reason": "State portal credentials are not configured for live filing.",
            }
        if not self._client:
            await self.connect()
        return await self._post("/professional-tax/returns", payload)
