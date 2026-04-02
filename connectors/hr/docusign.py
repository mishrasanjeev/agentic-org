"""DocuSign connector — HR / Document Signing.

Integrates with DocuSign eSignature REST API v2.1 for sending
documents for signature, tracking status, and retrieving signed copies.
Uses JWT Grant for server-to-server authentication.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class DocuSignConnector(BaseConnector):
    name = "docusign"
    category = "hr"
    auth_type = "jwt"
    base_url = "https://na4.docusign.net/restapi/v2.1"
    rate_limit_rpm = 100

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._account_id = self.config.get("account_id", "")

    def _register_tools(self):
        self._tool_registry["send_envelope"] = self.send_envelope
        self._tool_registry["get_envelope_status"] = self.get_envelope_status
        self._tool_registry["void_envelope"] = self.void_envelope
        self._tool_registry["download_document"] = self.download_document
        self._tool_registry["list_templates"] = self.list_templates
        self._tool_registry["create_envelope_from_template"] = self.create_envelope_from_template

    async def _authenticate(self):
        # DocuSign JWT Grant flow
        # For simplicity, use pre-generated access token from config
        # In production, implement JWT assertion with RSA key
        access_token = self._get_secret("access_token")
        self._auth_headers = {"Authorization": f"Bearer {access_token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get(f"/accounts/{self._account_id}")
            return {"status": "healthy", "account": result.get("accountName", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def send_envelope(self, **params) -> dict[str, Any]:
        """Send a document for signature.

        Params: emailSubject (required), documents (list of {name, documentBase64, documentId}),
                recipients (dict with signers list: [{email, name, recipientId, routingOrder}]),
                status (sent/created — 'sent' triggers immediately).
        """
        return await self._post(f"/accounts/{self._account_id}/envelopes", params)

    async def get_envelope_status(self, **params) -> dict[str, Any]:
        """Get status of an envelope.

        Params: envelope_id (required).
        """
        envelope_id = params["envelope_id"]
        return await self._get(f"/accounts/{self._account_id}/envelopes/{envelope_id}")

    async def void_envelope(self, **params) -> dict[str, Any]:
        """Void (cancel) an envelope.

        Params: envelope_id (required), voidedReason (required).
        """
        envelope_id = params.pop("envelope_id")
        return await self._put(
            f"/accounts/{self._account_id}/envelopes/{envelope_id}",
            {"status": "voided", "voidedReason": params.get("voidedReason", "Cancelled")},
        )

    async def download_document(self, **params) -> dict[str, Any]:
        """Get download URL for a signed document.

        Params: envelope_id (required), document_id (required, or "combined" for all).
        """
        envelope_id = params["envelope_id"]
        doc_id = params.get("document_id", "combined")
        # Returns binary PDF — for now return the URL for the caller to fetch
        return {
            "download_url": f"{self.base_url}/accounts/{self._account_id}/envelopes/{envelope_id}/documents/{doc_id}",
            "envelope_id": envelope_id,
            "document_id": doc_id,
        }

    async def list_templates(self, **params) -> dict[str, Any]:
        """List available templates.

        Params: search_text (optional), count (default 50).
        """
        params.setdefault("count", "50")
        return await self._get(f"/accounts/{self._account_id}/templates", params)

    async def create_envelope_from_template(self, **params) -> dict[str, Any]:
        """Create and send an envelope from a template.

        Params: templateId (required), emailSubject,
                templateRoles (list of {email, name, roleName}),
                status (sent/created).
        """
        return await self._post(f"/accounts/{self._account_id}/envelopes", params)
