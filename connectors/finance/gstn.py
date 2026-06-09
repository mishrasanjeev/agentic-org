"""GSTN connector — finance.

Integrates with GST Network via Adaequare GSP (GST Suvidha Provider).
Uses Adaequare's GSP app-token auth flow:
  1. POST to /gsp/authenticate?grant_type=token with GSP app credentials
  2. Use Authorization: Bearer <access_token> for subsequent API calls

Filing operations (GSTR-3B, GSTR-9) are signed with DSC when a
certificate path is configured (``dsc_path`` in config).
"""

from __future__ import annotations

import json
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
        self._access_token = ""

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

    def _get_gsp_secret(self, *keys: str) -> str:
        """Fetch GSTN credentials without BaseConnector's api_key fallback.

        ``BaseConnector._get_secret("client_secret")`` falls back to
        ``api_key`` when the requested key is absent. That fallback is useful
        for single-token connectors, but it is dangerous here because the
        Adaequare flow needs distinct app id and app secret values.
        """
        prefixes = (self.name.upper(), "GSTN", "GSTN_SANDBOX")
        for key in keys:
            candidates = [key, *(f"{prefix}_{key.upper()}" for prefix in prefixes)]
            for candidate in candidates:
                value = self.config.get(candidate)
                if value is not None and str(value).strip():
                    return str(value).strip()

            per_key_ref = self.config.get(f"secret_ref_{key}", "")
            if per_key_ref:
                value = self._resolve_gcp_secret(str(per_key_ref), key)
                if value and str(value).strip():
                    return str(value).strip()

            global_ref = self.config.get("secret_ref", "")
            if global_ref:
                value = self._resolve_gcp_secret(str(global_ref), key)
                if value and str(value).strip():
                    return str(value).strip()
        return ""

    async def _authenticate(self):
        """Authenticate with Adaequare GSP and prepare Bearer headers."""
        gsp_app_id = self._get_gsp_secret(
            "gspappid", "gsp_app_id", "client_id", "api_key", "aspid", "gsp_api_key"
        )
        gsp_app_secret = self._get_gsp_secret(
            "gspappsecret", "gsp_app_secret", "client_secret", "api_secret", "gsp_api_secret"
        )
        gstin = self._get_gsp_secret("gstin")

        if not gsp_app_id or not gsp_app_secret:
            raise ValueError(
                "GSTN Adaequare auth requires gspappid/client_id and "
                "gspappsecret/client_secret credentials."
            )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._provider_base_url}/authenticate?grant_type=token",
                json={},
                headers={
                    "gspappid": gsp_app_id,
                    "gspappsecret": gsp_app_secret,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            body = resp.json()
            token = body.get("access_token") or body.get("auth-token")
            legacy_auth_token = "access_token" not in body and bool(body.get("auth-token"))

        if not token or not str(token).strip():
            raise ValueError("GSTN Adaequare auth response did not include an access token.")

        self._access_token = str(token).strip()
        self._auth_headers = {
            "Authorization": f"Bearer {self._access_token}",
            "gstin": gstin,
            "Content-Type": "application/json",
        }
        if not gstin:
            self._auth_headers.pop("gstin", None)
        if legacy_auth_token:
            self._auth_headers["auth-token"] = self._access_token

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
                headers={
                    **self._auth_headers,
                    **dsc_headers,
                    "Content-Type": "application/json",
                },
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
