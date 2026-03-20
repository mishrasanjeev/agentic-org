"""Twilio connector — comms."""
from __future__ import annotations
import base64
from typing import Any
from connectors.framework.base_connector import BaseConnector

class TwilioConnector(BaseConnector):
    name = "twilio"
    category = "comms"
    auth_type = "api_key_secret"
    base_url = "https://api.twilio.com/2010-04-01"
    rate_limit_rpm = 100

    def _register_tools(self):
    self._tool_registry["make_outbound_call"] = self.make_outbound_call
    self._tool_registry["send_sms"] = self.send_sms
    self._tool_registry["send_whatsapp_message"] = self.send_whatsapp_message
    self._tool_registry["get_call_recording_url"] = self.get_call_recording_url
    self._tool_registry["trigger_tts_call_with_script"] = self.trigger_tts_call_with_script

    async def _authenticate(self):
        key_id = self._get_secret("key_id")
        key_secret = self._get_secret("key_secret")
        credentials = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._auth_headers = {"Authorization": f"Basic {credentials}"}

async def make_outbound_call(self, **params):
    """Execute make_outbound_call on twilio."""
    return await self._post("/make/outbound/call", params)


async def send_sms(self, **params):
    """Execute send_sms on twilio."""
    return await self._post("/send/sms", params)


async def send_whatsapp_message(self, **params):
    """Execute send_whatsapp_message on twilio."""
    return await self._post("/send/whatsapp/message", params)


async def get_call_recording_url(self, **params):
    """Execute get_call_recording_url on twilio."""
    return await self._post("/get/call/recording/url", params)


async def trigger_tts_call_with_script(self, **params):
    """Execute trigger_tts_call_with_script on twilio."""
    return await self._post("/trigger/tts/call/with/script", params)

