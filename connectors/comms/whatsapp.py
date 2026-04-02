"""WhatsApp Business connector — comms.

Integrates with WhatsApp Business Cloud API (Meta) for sending
template messages, text messages, and media messages.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class WhatsappConnector(BaseConnector):
    name = "whatsapp"
    category = "comms"
    auth_type = "meta_business"
    base_url = "https://graph.facebook.com/v21.0"
    rate_limit_rpm = 100

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._phone_number_id = self.config.get("phone_number_id", "")

    def _register_tools(self):
        self._tool_registry["send_template_message"] = self.send_template_message
        self._tool_registry["send_text_message"] = self.send_text_message
        self._tool_registry["send_media_message"] = self.send_media_message
        self._tool_registry["get_message_templates"] = self.get_message_templates
        self._tool_registry["get_business_profile"] = self.get_business_profile

    async def _authenticate(self):
        access_token = self._get_secret("access_token")
        self._auth_headers = {"Authorization": f"Bearer {access_token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get(f"/{self._phone_number_id}")
            return {"status": "healthy", "phone": result.get("display_phone_number", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def send_template_message(self, **params) -> dict[str, Any]:
        """Send a pre-approved WhatsApp template message.

        Params: to (required — phone number with country code, e.g. "919876543210"),
                template_name (required), language_code (default "en"),
                components (optional — list of template component values).
        """
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": params["to"],
            "type": "template",
            "template": {
                "name": params["template_name"],
                "language": {"code": params.get("language_code", "en")},
            },
        }
        if params.get("components"):
            body["template"]["components"] = params["components"]
        return await self._post(f"/{self._phone_number_id}/messages", body)

    async def send_text_message(self, **params) -> dict[str, Any]:
        """Send a text message (only to users who have messaged within 24h).

        Params: to (required), body (required — message text),
                preview_url (optional bool — render link previews).
        """
        return await self._post(f"/{self._phone_number_id}/messages", {
            "messaging_product": "whatsapp",
            "to": params["to"],
            "type": "text",
            "text": {"body": params["body"], "preview_url": params.get("preview_url", False)},
        })

    async def send_media_message(self, **params) -> dict[str, Any]:
        """Send a media message (image, document, video, audio).

        Params: to (required), type (image/document/video/audio),
                media_url or media_id (one required), caption (optional).
        """
        media_type = params.get("type", "document")
        media_content: dict[str, Any] = {}
        if params.get("media_url"):
            media_content["link"] = params["media_url"]
        elif params.get("media_id"):
            media_content["id"] = params["media_id"]
        if params.get("caption"):
            media_content["caption"] = params["caption"]

        return await self._post(f"/{self._phone_number_id}/messages", {
            "messaging_product": "whatsapp",
            "to": params["to"],
            "type": media_type,
            media_type: media_content,
        })

    async def get_message_templates(self, **params) -> dict[str, Any]:
        """List available message templates.

        Params: business_id (required), limit (default 20).
        """
        business_id = params.get("business_id", self.config.get("business_id", ""))
        return await self._get(f"/{business_id}/message_templates", {"limit": params.get("limit", 20)})

    async def get_business_profile(self, **params) -> dict[str, Any]:
        """Get WhatsApp Business profile."""
        return await self._get(f"/{self._phone_number_id}/whatsapp_business_profile", {"fields": "about,address,description,email,websites"})
