"""Darwinbox connector — HR.

Integrates with Darwinbox HRMS API for employee management,
payroll, attendance, leave, and org structure.
Note: Darwinbox uses POST for ALL operations including reads.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class DarwinboxConnector(BaseConnector):
    name = "darwinbox"
    category = "hr"
    auth_type = "api_key_oauth2"
    base_url = "https://org.darwinbox.in/api/v1"
    rate_limit_rpm = 200

    def _register_tools(self):
        self._tool_registry["get_employee"] = self.get_employee
        self._tool_registry["create_employee"] = self.create_employee
        self._tool_registry["run_payroll"] = self.run_payroll
        self._tool_registry["get_attendance"] = self.get_attendance
        self._tool_registry["apply_leave"] = self.apply_leave
        self._tool_registry["get_org_chart"] = self.get_org_chart
        self._tool_registry["update_performance"] = self.update_performance
        self._tool_registry["terminate_employee"] = self.terminate_employee
        self._tool_registry["transfer_employee"] = self.transfer_employee
        self._tool_registry["get_payslip"] = self.get_payslip

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        api_secret = self._get_secret("api_secret")
        self._auth_headers = {
            "Authorization": f"Bearer {api_key}",
            "X-API-Secret": api_secret,
            "Content-Type": "application/json",
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._post("/employees/getEmployee", {"employee_id": "test"})
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # Note: Darwinbox uses POST for reads — this is by design, not a mistake
    async def get_employee(self, **params) -> dict[str, Any]:
        """Get employee details (Darwinbox uses POST for reads).

        Params: employee_id (required).
        """
        return await self._post("/employees/getEmployee", params)

    async def create_employee(self, **params) -> dict[str, Any]:
        """Create a new employee record.

        Params: first_name, last_name, email, date_of_joining, department,
                designation, reporting_manager_id, employment_type.
        """
        return await self._post("/employees/create", params)

    async def run_payroll(self, **params) -> dict[str, Any]:
        """Trigger payroll processing for a month.

        Params: month (MM), year (YYYY), department (optional).
        """
        return await self._post("/payroll/run", params)

    async def get_attendance(self, **params) -> dict[str, Any]:
        """Get attendance records (POST-based read).

        Params: employee_id (required), from_date (YYYY-MM-DD), to_date.
        """
        return await self._post("/attendance/getAttendance", params)

    async def apply_leave(self, **params) -> dict[str, Any]:
        """Apply for leave on behalf of an employee.

        Params: employee_id, leave_type, from_date, to_date, reason.
        """
        return await self._post("/leave/apply", params)

    async def get_org_chart(self, **params) -> dict[str, Any]:
        """Get organizational hierarchy (POST-based read).

        Params: department (optional), level (optional).
        """
        return await self._post("/org/hierarchy", params)

    async def update_performance(self, **params) -> dict[str, Any]:
        """Update performance goal/rating.

        Params: employee_id, goal_id, rating, comments.
        """
        return await self._post("/performance/updateGoal", params)

    async def terminate_employee(self, **params) -> dict[str, Any]:
        """Terminate an employee.

        Params: employee_id, last_working_date, reason, notice_period_days.
        """
        return await self._post("/employees/terminate", params)

    async def transfer_employee(self, **params) -> dict[str, Any]:
        """Transfer an employee to a new department/location.

        Params: employee_id, new_department, new_designation,
                new_reporting_manager_id, effective_date.
        """
        return await self._post("/employees/transfer", params)

    async def get_payslip(self, **params) -> dict[str, Any]:
        """Get payslip for an employee (POST-based read).

        Params: employee_id, month (MM), year (YYYY).
        """
        return await self._post("/payroll/getPayslip", params)
