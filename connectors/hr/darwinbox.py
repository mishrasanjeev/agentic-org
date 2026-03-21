"""Darwinbox connector — hr."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class DarwinboxConnector(BaseConnector):
    name = "darwinbox"
    category = "hr"
    auth_type = "api_key_oauth2"
    base_url = "https://org.darwinbox.in/api"
    rate_limit_rpm = 200

    def _register_tools(self):
        self._tool_registry["get_employee"] = self.get_employee
        self._tool_registry["create_employee"] = self.create_employee
        self._tool_registry["run_payroll"] = self.run_payroll
        self._tool_registry["get_attendance"] = self.get_attendance
        self._tool_registry["post_leave"] = self.post_leave
        self._tool_registry["get_org_chart"] = self.get_org_chart
        self._tool_registry["update_performance"] = self.update_performance
        self._tool_registry["terminate_employee"] = self.terminate_employee
        self._tool_registry["transfer_employee"] = self.transfer_employee
        self._tool_registry["get_payslip"] = self.get_payslip

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

    async def get_employee(self, **params):
        """Execute get_employee on darwinbox."""
        return await self._post("/get/employee", params)


    async def create_employee(self, **params):
        """Execute create_employee on darwinbox."""
        return await self._post("/create/employee", params)


    async def run_payroll(self, **params):
        """Execute run_payroll on darwinbox."""
        return await self._post("/run/payroll", params)


    async def get_attendance(self, **params):
        """Execute get_attendance on darwinbox."""
        return await self._post("/get/attendance", params)


    async def post_leave(self, **params):
        """Execute post_leave on darwinbox."""
        return await self._post("/post/leave", params)


    async def get_org_chart(self, **params):
        """Execute get_org_chart on darwinbox."""
        return await self._post("/get/org/chart", params)


    async def update_performance(self, **params):
        """Execute update_performance on darwinbox."""
        return await self._post("/update/performance", params)


    async def terminate_employee(self, **params):
        """Execute terminate_employee on darwinbox."""
        return await self._post("/terminate/employee", params)


    async def transfer_employee(self, **params):
        """Execute transfer_employee on darwinbox."""
        return await self._post("/transfer/employee", params)


    async def get_payslip(self, **params):
        """Execute get_payslip on darwinbox."""
        return await self._post("/get/payslip", params)

