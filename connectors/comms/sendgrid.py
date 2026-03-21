"""Sendgrid connector — comms."""

from __future__ import annotations

from connectors.framework.base_connector import BaseConnector


class SendgridConnector(BaseConnector):
    name = "sendgrid"
    category = "comms"
    auth_type = "api_key"
    base_url = "https://api.sendgrid.com/v3"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["send_transactional_email"] = self.send_transactional_email
        self._tool_registry["create_email_template"] = self.create_email_template
        self._tool_registry["get_delivery_statistics"] = self.get_delivery_statistics
        self._tool_registry["manage_suppression_list"] = self.manage_suppression_list
        self._tool_registry["validate_email_address"] = self.validate_email_address

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

    async def send_transactional_email(self, **params):
        """Execute send_transactional_email on sendgrid."""
        return await self._post("/send/transactional/email", params)

    async def create_email_template(self, **params):
        """Execute create_email_template on sendgrid."""
        return await self._post("/create/email/template", params)

    async def get_delivery_statistics(self, **params):
        """Execute get_delivery_statistics on sendgrid."""
        return await self._post("/get/delivery/statistics", params)

    async def manage_suppression_list(self, **params):
        """Execute manage_suppression_list on sendgrid."""
        return await self._post("/manage/suppression/list", params)

    async def validate_email_address(self, **params):
        """Execute validate_email_address on sendgrid."""
        return await self._post("/validate/email/address", params)
