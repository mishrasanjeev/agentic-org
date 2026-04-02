"""Twilio connector — comms.

Integrates with Twilio REST API for SMS, voice calls, and WhatsApp
messaging. Uses Basic auth with Account SID + Auth Token.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class TwilioConnector(BaseConnector):
    name = "twilio"
    category = "comms"
    auth_type = "api_key_secret"
    base_url = "https://api.twilio.com/2010-04-01"
    rate_limit_rpm = 100

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._account_sid = self.config.get("account_sid", "")

    def _register_tools(self):
        self._tool_registry["send_sms"] = self.send_sms
        self._tool_registry["make_call"] = self.make_call
        self._tool_registry["send_whatsapp"] = self.send_whatsapp
        self._tool_registry["get_recordings"] = self.get_recordings
        self._tool_registry["get_message_status"] = self.get_message_status

    async def _authenticate(self):
        import base64
        account_sid = self._get_secret("account_sid")
        auth_token = self._get_secret("auth_token")
        self._account_sid = account_sid
        credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        self._auth_headers = {"Authorization": f"Basic {credentials}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get(f"/Accounts/{self._account_sid}.json")
            return {"status": "healthy", "account": result.get("friendly_name", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def send_sms(self, **params) -> dict[str, Any]:
        """Send an SMS message.

        Params: to (required — E.164 format like +919876543210),
                body (required — message text),
                from_number (optional — defaults to config["from_number"]).
        """
        from_num = params.get("from_number", self.config.get("from_number", ""))
        return await self._post_form(
            f"/Accounts/{self._account_sid}/Messages.json",
            {"To": params["to"], "From": from_num, "Body": params["body"]},
        )

    async def make_call(self, **params) -> dict[str, Any]:
        """Initiate an outbound voice call.

        Params: to (required — E.164), twiml (optional — TwiML instructions),
                url (optional — TwiML URL), from_number.
        """
        from_num = params.get("from_number", self.config.get("from_number", ""))
        data: dict[str, str] = {"To": params["to"], "From": from_num}
        if params.get("twiml"):
            data["Twiml"] = params["twiml"]
        elif params.get("url"):
            data["Url"] = params["url"]
        return await self._post_form(f"/Accounts/{self._account_sid}/Calls.json", data)

    async def send_whatsapp(self, **params) -> dict[str, Any]:
        """Send a WhatsApp message via Twilio.

        Params: to (required — phone number without whatsapp: prefix),
                body (required), from_number (optional).
        """
        from_num = params.get("from_number", self.config.get("whatsapp_from", ""))
        return await self._post_form(
            f"/Accounts/{self._account_sid}/Messages.json",
            {"To": f"whatsapp:{params['to']}", "From": f"whatsapp:{from_num}", "Body": params["body"]},
        )

    async def get_recordings(self, **params) -> dict[str, Any]:
        """Get call recordings.

        Params: call_sid (optional — all recordings if omitted),
                date_created (YYYY-MM-DD), limit (default 20).
        """
        query: dict[str, str] = {}
        if params.get("call_sid"):
            query["CallSid"] = params["call_sid"]
        if params.get("date_created"):
            query["DateCreated"] = params["date_created"]
        query["PageSize"] = str(params.get("limit", 20))
        return await self._get(f"/Accounts/{self._account_sid}/Recordings.json", query)

    async def get_message_status(self, **params) -> dict[str, Any]:
        """Get status of a sent message.

        Params: message_sid (required).
        """
        message_sid = params["message_sid"]
        return await self._get(f"/Accounts/{self._account_sid}/Messages/{message_sid}.json")
