"""Keka connector — HR.

Integrates with Keka HR API v1 for employee data, payroll,
leave management, and attendance. India-focused HRMS.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class KekaConnector(BaseConnector):
    name = "keka"
    category = "hr"
    auth_type = "api_key"
    base_url = "https://api.keka.com/v1"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["get_employee"] = self.get_employee
        self._tool_registry["list_employees"] = self.list_employees
        self._tool_registry["run_payroll"] = self.run_payroll
        self._tool_registry["get_leave_balance"] = self.get_leave_balance
        self._tool_registry["post_reimbursement"] = self.post_reimbursement
        self._tool_registry["get_attendance_summary"] = self.get_attendance_summary

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._get("/hris/employees", {"pageSize": "1"})
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def get_employee(self, **params) -> dict[str, Any]:
        """Get employee details by ID.

        Params: employee_id (required).
        """
        employee_id = params["employee_id"]
        return await self._get(f"/hris/employees/{employee_id}")

    async def list_employees(self, **params) -> dict[str, Any]:
        """List employees with optional filters.

        Params: department (optional), status (active/inactive),
                pageSize (default 50), pageNumber (default 1).
        """
        params.setdefault("pageSize", "50")
        params.setdefault("pageNumber", "1")
        return await self._get("/hris/employees", params)

    async def run_payroll(self, **params) -> dict[str, Any]:
        """Trigger payroll processing.

        Params: month (1-12), year (YYYY), paygroup_id (optional).
        """
        return await self._post("/payroll/run", params)

    async def get_leave_balance(self, **params) -> dict[str, Any]:
        """Get leave balance for an employee.

        Params: employee_id (required).
        """
        employee_id = params["employee_id"]
        return await self._get(f"/leave/balance/{employee_id}")

    async def post_reimbursement(self, **params) -> dict[str, Any]:
        """Submit an expense reimbursement.

        Params: employee_id, amount, category, description,
                receipt_url (optional), date.
        """
        return await self._post("/expense/reimbursement", params)

    async def get_attendance_summary(self, **params) -> dict[str, Any]:
        """Get attendance summary for an employee or team.

        Params: employee_id (optional — team summary if omitted),
                from_date (YYYY-MM-DD), to_date.
        """
        return await self._get("/attendance/summary", params)
