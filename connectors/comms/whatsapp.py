"""Whatsapp connector — comms."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class WhatsappConnector(BaseConnector):
    name = "whatsapp"
    category = "comms"
    auth_type = "meta_business"
    base_url = "https://graph.facebook.com/v21.0"
    rate_limit_rpm = 100

    def _register_tools(self):
    self._tool_registry["send_approved_template_message"] = self.send_approved_template_message
    self._tool_registry["send_interactive_message"] = self.send_interactive_message
    self._tool_registry["send_media_message"] = self.send_media_message
    self._tool_registry["get_delivery_status"] = self.get_delivery_status
    self._tool_registry["manage_opt_out"] = self.manage_opt_out

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

async def send_approved_template_message(self, **params):
    """Execute send_approved_template_message on whatsapp."""
    return await self._post("/send/approved/template/message", params)


async def send_interactive_message(self, **params):
    """Execute send_interactive_message on whatsapp."""
    return await self._post("/send/interactive/message", params)


async def send_media_message(self, **params):
    """Execute send_media_message on whatsapp."""
    return await self._post("/send/media/message", params)


async def get_delivery_status(self, **params):
    """Execute get_delivery_status on whatsapp."""
    return await self._post("/get/delivery/status", params)


async def manage_opt_out(self, **params):
    """Execute manage_opt_out on whatsapp."""
    return await self._post("/manage/opt/out", params)

