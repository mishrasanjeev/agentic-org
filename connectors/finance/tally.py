"""Tally connector — finance.

Communicates with Tally Prime / Tally ERP 9 via its native XML/TDL
protocol over HTTP.  Tally exposes a single HTTP POST endpoint on
localhost (default port 9000) and expects an XML envelope containing
TDL commands.  All requests and responses are XML — never JSON.

When the CA's Tally instance is on a remote machine, set ``bridge_url``
in the connector config to route requests through the cloud bridge
(WebSocket tunnel) instead of hitting localhost directly.
"""

from __future__ import annotations

import uuid
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

import structlog
from defusedxml.ElementTree import ParseError as XMLParseError
from defusedxml.ElementTree import fromstring as xml_fromstring

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


# UR-Bug-6 (Uday/Ramesh 2026-04-21): Tally errors used to surface as a
# bare ``RuntimeError`` — callers couldn't distinguish a transient
# bridge outage from a TDL-syntax bug, company-not-open, or bad XML.
# The UI rendered a single opaque "Tally bridge error" line for every
# case, so debugging was guesswork.
#
# Exception hierarchy keeps the generic catch-all working (``except
# TallyError``) while letting finer handlers react to specific
# failure modes. API layers should catch ``TallyError`` and map to
# HTTP 4xx/5xx according to the subclass.


class TallyError(RuntimeError):
    """Base class for all Tally-originated failures.

    Attributes
    ----------
    detail : str | None
        Machine-readable reason from Tally's XML response or the
        bridge envelope (e.g. "LINEERROR 01: Company not open").
    request_id : str | None
        Bridge request_id when the failure came through the tunnel —
        useful for cross-referencing bridge logs.
    """

    def __init__(
        self,
        message: str,
        *,
        detail: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.detail = detail
        self.request_id = request_id


class TallyConnectionError(TallyError):
    """Tally endpoint is unreachable (bridge down, ngrok offline,
    localhost:9000 closed, TCP timeout). Typically a user/ops problem
    rather than a code bug. API layer should return 503."""


class TallyBridgeError(TallyError):
    """Bridge rejected the call (auth failure, unknown bridge_id,
    bridge internal error)."""


class TallyResponseError(TallyError):
    """Tally itself returned an error response (TDL syntax, missing
    company, permission denied, malformed voucher). API layer should
    return 400 — the user's request is the problem."""


class TallyXMLError(TallyError):
    """Tally returned a non-XML or malformed-XML body. Usually means
    the bridge forwarded an HTML error page (502/504) or Tally is
    running on a port serving a non-TDL service."""


def _tdl_request(request_type: str, collection: str, filters: dict | None = None) -> str:
    """Build a Tally TDL XML request envelope."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    SubElement(header, "VERSION").text = "1"
    SubElement(header, "TALLYREQUEST").text = request_type
    SubElement(header, "TYPE").text = "Data"
    SubElement(header, "ID").text = collection

    body = SubElement(envelope, "BODY")
    desc = SubElement(body, "DESC")
    tdl = SubElement(desc, "TDL")
    tdl_msg = SubElement(tdl, "TDLMESSAGE")

    if filters:
        for key, value in filters.items():
            filt = SubElement(tdl_msg, "FILTER")
            filt.set("NAME", key)
            filt.text = str(value)

    return '<?xml version="1.0" encoding="utf-8"?>' + tostring(envelope, encoding="unicode")


def _import_request(object_type: str, data: dict) -> str:
    """Build a Tally import XML request (for posting vouchers, etc.)."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    SubElement(header, "VERSION").text = "1"
    SubElement(header, "TALLYREQUEST").text = "Import"
    SubElement(header, "TYPE").text = "Data"
    SubElement(header, "ID").text = "All Masters and Vouchers"

    body = SubElement(envelope, "BODY")
    import_data = SubElement(body, "IMPORTDATA")
    request_desc = SubElement(import_data, "REQUESTDESC")
    SubElement(request_desc, "REPORTNAME").text = "All Masters and Vouchers"
    static_vars = SubElement(request_desc, "STATICVARIABLES")
    SubElement(static_vars, "SVCURRENTCOMPANY").text = data.get("company", "")

    request_data = SubElement(import_data, "REQUESTDATA")
    obj = SubElement(request_data, object_type)
    for key, value in data.items():
        if key != "company":
            SubElement(obj, key.upper()).text = str(value)

    return '<?xml version="1.0" encoding="utf-8"?>' + tostring(envelope, encoding="unicode")


def _xml_to_dict(elem: Element) -> dict[str, Any]:
    """Flatten a Tally XML response element into a dict."""
    result: dict[str, Any] = {}
    for child in elem:
        if len(child):
            result[child.tag] = _xml_to_dict(child)
        else:
            result[child.tag] = child.text
    return result


# UR-Bug-6: inspect the parsed XML for Tally's inline error markers.
# Tally reports business-logic failures *inside* a 2xx response body
# rather than via HTTP status, so a naive caller that only checks the
# transport succeeds when the voucher actually failed.
_TALLY_ERROR_KEYS = frozenset({
    "LINEERROR",
    "EXCEPTIONS",
    "ERROR",
    "DESC",  # inside <STATUS><DESC> for some TDL errors
})


def _extract_tally_error(parsed: dict[str, Any]) -> str | None:
    """Return the first error message found anywhere in a parsed Tally
    response, or None if the response looks clean.

    Scans recursively because Tally nests errors at varying depths
    depending on the request type (Export vs Import vs TDL dump).
    """
    status = parsed.get("STATUS") if isinstance(parsed, dict) else None
    if isinstance(status, dict):
        # Success responses typically carry STATUS=1 as the text payload.
        # Anything else is suspicious; dig for DESC.
        desc = status.get("DESC")
        if isinstance(desc, str) and desc.strip():
            return desc.strip()
    for key in _TALLY_ERROR_KEYS:
        value = parsed.get(key) if isinstance(parsed, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _extract_tally_error(value)
            if nested:
                return nested
    # Depth-first walk for deeply nested IMPORTRESULT / LINEERROR blocks.
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, dict):
                nested = _extract_tally_error(v)
                if nested:
                    return nested
    return None


class TallyConnector(BaseConnector):
    """Tally connector with optional bridge routing for remote instances.

    Config options:
        bridge_url:   Cloud bridge endpoint (e.g. http://localhost:8000/api/v1/bridge/route/tally).
                      When set, XML requests are tunneled through the bridge to the
                      CA's local Tally instead of hitting localhost directly.
        bridge_id:    Bridge identifier (required when bridge_url is set).
        bridge_token: Auth token for the bridge (required when bridge_url is set).
    """

    name = "tally"
    category = "finance"
    auth_type = "tdl_xml"
    base_url = "http://localhost:9000"
    rate_limit_rpm = 60

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._bridge_url = self.config.get("bridge_url", "")
        self._bridge_id = self.config.get("bridge_id", "")
        self._bridge_token = self.config.get("bridge_token", "")
        self._use_bridge = bool(self._bridge_url and self._bridge_id)

    def _register_tools(self):
        self._tool_registry["post_voucher"] = self.post_voucher
        self._tool_registry["get_ledger_balance"] = self.get_ledger_balance
        self._tool_registry["generate_gst_report"] = self.generate_gst_report
        self._tool_registry["export_tally_xml_data"] = self.export_tally_xml_data
        self._tool_registry["get_trial_balance"] = self.get_trial_balance
        self._tool_registry["get_stock_summary"] = self.get_stock_summary

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"X-Tally-Key": api_key} if api_key else {}

    async def health_check(self) -> dict[str, Any]:
        """Tally-specific health check.

        UR-Bug-4 (Uday/Ramesh 2026-04-21): the inherited BaseConnector
        default issues an HTTP GET to ``/`` — Tally Prime only serves
        HTTP POST with an XML envelope and returns 405 on GET, so the
        default always reported "unhealthy". Override sends the
        smallest possible TDL probe (``CompanyInfo`` export) through
        whichever path is configured (bridge or direct) and treats a
        parseable XML response as healthy.
        """
        probe = _tdl_request("Export", "CompanyInfo")
        try:
            if self._use_bridge:
                await self._send_via_bridge(probe)
                return {"status": "healthy", "transport": "bridge"}
            # Direct localhost / LAN call. Swallow the dict — we only
            # care that the request cycle completes and the response
            # parses as XML.
            resp = await self._post_xml(probe)
            if resp is None:
                return {"status": "unhealthy", "error": "empty XML response"}
            return {"status": "healthy", "transport": "direct"}
        except TallyConnectionError as exc:
            # Upstream is unreachable — ops-level failure, not an auth
            # or data issue. Report as "unreachable" not "unhealthy" so
            # the UI can phrase it correctly.
            return {
                "status": "unreachable",
                "error": str(exc),
                "detail": exc.detail,
            }
        except TallyBridgeError as exc:
            return {
                "status": "bridge_error",
                "error": str(exc),
                "detail": exc.detail,
            }
        except TallyError as exc:
            return {
                "status": "unhealthy",
                "error": str(exc),
                "detail": exc.detail,
            }
        except Exception as exc:  # defensive — connector health must never raise
            return {"status": "unhealthy", "error": str(exc)}

    async def _send_xml(self, xml_body: str) -> dict[str, Any]:
        """Send XML to Tally — directly or via bridge tunnel.

        When bridge is configured, wraps the XML in a JSON envelope
        and POSTs to the bridge routing endpoint.  The bridge forwards
        the XML to the CA's local Tally and returns the response.
        """
        if self._use_bridge:
            return await self._send_via_bridge(xml_body)
        resp = await self._post_xml(xml_body)
        return _xml_to_dict(resp)

    async def _send_via_bridge(self, xml_body: str) -> dict[str, Any]:
        """Route XML through the cloud bridge to a remote Tally instance.

        UR-Bug-6: each failure mode now maps to a specific Tally*Error
        subclass so the API layer can pick the right HTTP status and
        surface a user-actionable message instead of a generic 500.
        """
        import httpx

        request_id = str(uuid.uuid4())
        payload = {
            "request_id": request_id,
            "bridge_id": self._bridge_id,
            "method": "post_xml",
            "xml_body": xml_body,
        }
        headers = {"Authorization": f"Bearer {self._bridge_token}"}

        logger.info(
            "tally_bridge_request",
            request_id=request_id,
            bridge_id=self._bridge_id,
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    self._bridge_url,
                    json=payload,
                    headers=headers,
                )
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            raise TallyConnectionError(
                "Could not reach the Tally bridge at "
                f"{self._bridge_url} — check that the AgenticOrg Tally "
                "Bridge is running on the client's machine and the ngrok "
                "tunnel is live.",
                detail=str(exc),
                request_id=request_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise TallyBridgeError(
                f"Tally bridge HTTP error: {exc}",
                detail=str(exc),
                request_id=request_id,
            ) from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise TallyBridgeError(
                "Tally bridge rejected our token. Regenerate bridge "
                "credentials in Settings → Tally Connection.",
                detail=f"HTTP {resp.status_code}",
                request_id=request_id,
            )
        if resp.status_code >= 500:
            raise TallyBridgeError(
                f"Tally bridge returned HTTP {resp.status_code} — "
                "the bridge itself may be in a bad state; try restarting "
                "the agenticorg-bridge process.",
                detail=resp.text[:500],
                request_id=request_id,
            )
        if resp.status_code >= 400:
            raise TallyBridgeError(
                f"Tally bridge rejected the request (HTTP "
                f"{resp.status_code}).",
                detail=resp.text[:500],
                request_id=request_id,
            )

        try:
            result = resp.json()
        except ValueError as exc:
            raise TallyBridgeError(
                "Tally bridge returned a non-JSON response — most likely "
                "an HTML error page from an upstream proxy.",
                detail=resp.text[:500],
                request_id=request_id,
            ) from exc

        if result.get("status") == "error":
            raise TallyBridgeError(
                f"Tally bridge error: {result.get('error', 'unknown')}",
                detail=result.get("error"),
                request_id=request_id,
            )

        xml_response = result.get("xml_response", "")
        if not xml_response:
            raise TallyResponseError(
                "Tally returned an empty response — usually means no "
                "company is open on the client's Tally instance.",
                request_id=request_id,
            )

        try:
            elem = xml_fromstring(xml_response)
        except XMLParseError as exc:
            raise TallyXMLError(
                "Tally returned a non-XML body. Check that the Tally "
                "instance is actually Tally Prime / ERP 9 and not "
                "another service listening on the port.",
                detail=str(exc),
                request_id=request_id,
            ) from exc

        parsed = _xml_to_dict(elem)

        # Tally surfaces business-logic failures inline in the response
        # (LINEERROR, EXCEPTIONS, etc.). Lift those into TallyResponseError
        # so callers don't treat a failed voucher as a success.
        err_msg = _extract_tally_error(parsed)
        if err_msg:
            raise TallyResponseError(
                f"Tally rejected the request: {err_msg}",
                detail=err_msg,
                request_id=request_id,
            )

        return parsed

    async def post_voucher(self, **params) -> dict[str, Any]:
        """Import a voucher into Tally via TDL XML."""
        xml_body = _import_request("TALLYMESSAGE", params)
        return await self._send_xml(xml_body)

    async def get_ledger_balance(self, **params) -> dict[str, Any]:
        """Fetch ledger balances via TDL export request."""
        xml_body = _tdl_request("Export", "Ledger", params)
        return await self._send_xml(xml_body)

    async def generate_gst_report(self, **params) -> dict[str, Any]:
        """Generate GST report via TDL export request."""
        xml_body = _tdl_request("Export", "GSTReport", params)
        return await self._send_xml(xml_body)

    async def export_tally_xml_data(self, **params) -> dict[str, Any]:
        """Export raw XML data from Tally."""
        collection = params.pop("collection", "All Masters and Vouchers")
        xml_body = _tdl_request("Export", collection, params)
        return await self._send_xml(xml_body)

    async def get_trial_balance(self, **params) -> dict[str, Any]:
        """Fetch trial balance via TDL export request."""
        xml_body = _tdl_request("Export", "TrialBalance", params)
        return await self._send_xml(xml_body)

    async def get_stock_summary(self, **params) -> dict[str, Any]:
        """Fetch stock summary via TDL export request."""
        xml_body = _tdl_request("Export", "StockSummary", params)
        return await self._send_xml(xml_body)
