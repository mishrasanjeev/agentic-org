"""Keka connector — hr."""

from __future__ import annotations

from connectors.framework.base_connector import BaseConnector


class KekaConnector(BaseConnector):
    name = "keka"
    category = "hr"
    auth_type = "api_key"
    base_url = "https://api.keka.com/v1"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["get_employee"] = self.get_employee
        self._tool_registry["run_payroll"] = self.run_payroll
        self._tool_registry["get_leave_balance"] = self.get_leave_balance
        self._tool_registry["post_reimbursement"] = self.post_reimbursement
        self._tool_registry["get_tds_workings"] = self.get_tds_workings
        self._tool_registry["get_attendance_summary"] = self.get_attendance_summary

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

    async def get_employee(self, **params):
        """Execute get_employee on keka."""
        return await self._post("/get/employee", params)

    async def run_payroll(self, **params):
        """Execute run_payroll on keka."""
        return await self._post("/run/payroll", params)

    async def get_leave_balance(self, **params):
        """Execute get_leave_balance on keka."""
        return await self._post("/get/leave/balance", params)

    async def post_reimbursement(self, **params):
        """Execute post_reimbursement on keka."""
        return await self._post("/post/reimbursement", params)

    async def get_tds_workings(self, **params):
        """Execute get_tds_workings on keka."""
        return await self._post("/get/tds/workings", params)

    async def get_attendance_summary(self, **params):
        """Execute get_attendance_summary on keka."""
        return await self._post("/get/attendance/summary", params)
