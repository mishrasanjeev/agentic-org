"""GSTN connector — finance.

Integrates with GST Network via Adaequare GSP (GST Suvidha Provider).
Uses the proper 2-step auth flow:
  1. POST to /gsp/authenticate with API key to obtain a session token
  2. Use the session token (auth-token header) for all subsequent API calls

Filing operations (GSTR-3B, GSTR-9) are signed with DSC when a
certificate path is configured (``dsc_path`` in config).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()

GSTN_API_BASE_URL = "https://gsp.adaequare.com/gsp"
GSTN_SANDBOX_API_BASE_URL = "https://gsp.adaequare.com/test/enriched/gsp"
GSTN_ALLOWED_BASE_URLS = {GSTN_API_BASE_URL, GSTN_SANDBOX_API_BASE_URL}


def _provider_base_url(connector_cls: type[GstnConnector]) -> str:
    base_url = getattr(connector_cls, "base_url", GSTN_API_BASE_URL)
    if base_url not in GSTN_ALLOWED_BASE_URLS:
        raise ValueError("GSTN base URL must be an Adaequare provider endpoint")
    return base_url


class GstnConnector(BaseConnector):
    name = "gstn"
    category = "finance"
    auth_type = "gsp_dsc"
    base_url = GSTN_API_BASE_URL
    rate_limit_rpm = 50

    def __init__(self, config: dict[str, Any] | None = None):
        provider_base_url = _provider_base_url(type(self))
        safe_config = dict(config or {})
        safe_config["base_url"] = provider_base_url
        super().__init__(safe_config)
        self._provider_base_url = provider_base_url
        self._dsc_adapter = None

        dsc_path = self.config.get("dsc_path", "")
        if dsc_path:
            from connectors.framework.auth_adapters import DSCAdapter

            self._dsc_adapter = DSCAdapter(
                dsc_path=dsc_path,
                dsc_password=self.config.get("dsc_password", ""),
            )

    def _register_tools(self):
        self._tool_registry["fetch_gstr2a"] = self.fetch_gstr2a
        self._tool_registry["push_gstr1_data"] = self.push_gstr1_data
        self._tool_registry["file_gstr3b"] = self.file_gstr3b
        self._tool_registry["file_gstr9"] = self.file_gstr9
        self._tool_registry["generate_eway_bill"] = self.generate_eway_bill
        self._tool_registry["generate_einvoice_irn"] = self.generate_einvoice_irn
        self._tool_registry["check_filing_status"] = self.check_filing_status
        self._tool_registry["get_compliance_notice"] = self.get_compliance_notice

    async def _authenticate(self):
        """Adaequare GSP 2-step auth: authenticate to get session token."""
        api_key = self._get_secret("api_key")
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        if api_key and client_id == api_key and "client_id" not in self.config:
            client_id = ""
        if api_key and client_secret == api_key and "client_secret" not in self.config:
            client_secret = ""
        gstin = self._get_secret("gstin")
        username = self._get_secret("username")
        password = self._get_secret("password")
        state_code = str(gstin or "")[:2]

        if client_id and client_secret:
            auth_headers = {
                "clientid": client_id,
                "client-secret": client_secret,
                "Content-Type": "application/json",
            }
            if state_code:
                auth_headers["state-cd"] = state_code
            auth_headers["txn"] = uuid.uuid4().hex
        elif api_key:
            auth_headers = {
                "aspid": api_key,
                "Content-Type": "application/json",
            }
        else:
            raise ValueError("GSTN requires api_key or client_id/client_secret")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._provider_base_url}/authenticate",
                json={
                    "action": "ACCESSTOKEN",
                    "username": username,
                    "password": password,
                },
                headers=auth_headers,
            )
            resp.raise_for_status()
            auth_token = resp.json()["auth-token"]

        self._auth_headers = {
            "auth-token": auth_token,
            "gstin": gstin,
            "Content-Type": "application/json",
        }
        if client_id and client_secret:
            self._auth_headers.update(
                {
                    "clientid": client_id,
                    "client-secret": client_secret,
                }
            )
            if state_code:
                self._auth_headers["state-cd"] = state_code
        else:
            self._auth_headers["aspid"] = api_key

    async def _sign_and_post(self, path: str, data: dict) -> dict[str, Any]:
        """Sign the payload with DSC and POST to the GSP endpoint.

        Used for filing operations (GSTR-3B, GSTR-9) that require
        Digital Signature Certificate authentication.
        """
        payload_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")

        if self._dsc_adapter:
            dsc_headers = await self._dsc_adapter.sign_and_get_headers(payload_bytes)
            logger.info("gstn_dsc_signed", path=path)

            if not self._client:
                raise RuntimeError("Connector not connected")

            resp = await self._client.post(
                path,
                content=payload_bytes,
                headers={**dsc_headers, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

        # Fallback: no DSC configured — post without signature (sandbox/testing)
        logger.warning("gstn_filing_without_dsc", path=path)
        return await self._post(path, data)

    async def fetch_gstr2a(self, **params):
        """Fetch GSTR-2A inbound supplies data."""
        return await self._get("/returns/gstr2a", params)

    async def push_gstr1_data(self, **params):
        """Push GSTR-1 outbound supplies data."""
        return await self._post("/returns/gstr1", params)

    async def file_gstr3b(self, **params):
        """File GSTR-3B monthly summary return (DSC-signed)."""
        return await self._sign_and_post("/returns/gstr3b", params)

    async def file_gstr9(self, **params):
        """File GSTR-9 annual return (DSC-signed)."""
        return await self._sign_and_post("/returns/gstr9", params)

    async def generate_eway_bill(self, **params):
        """Generate E-Way Bill for goods movement."""
        return await self._post("/ewaybill/generate", params)

    async def generate_einvoice_irn(self, **params):
        """Generate E-Invoice IRN (Invoice Reference Number)."""
        return await self._post("/einvoice/generate", params)

    async def check_filing_status(self, **params):
        """Check GST filing status for a return period."""
        return await self._get("/returns/status", params)

    async def get_compliance_notice(self, **params):
        """Fetch GST compliance notices."""
        return await self._get("/compliance/notices", params)

    def get_dsc_info(self) -> dict[str, Any]:
        """Return DSC certificate details (for verifying before filing)."""
        if not self._dsc_adapter:
            return {"error": "No DSC configured — set dsc_path in connector config"}
        return self._dsc_adapter.verify_certificate()
