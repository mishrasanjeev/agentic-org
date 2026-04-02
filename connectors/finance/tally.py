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
from xml.etree.ElementTree import fromstring as xml_fromstring  # noqa: S314

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


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
        """Route XML through the cloud bridge to a remote Tally instance."""
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

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                self._bridge_url,
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            result = resp.json()

        if result.get("status") == "error":
            raise RuntimeError(
                f"Tally bridge error: {result.get('error', 'unknown')}"
            )

        xml_response = result.get("xml_response", "")
        if not xml_response:
            raise RuntimeError("Tally bridge returned empty response")

        elem = xml_fromstring(xml_response)  # noqa: S314
        return _xml_to_dict(elem)

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
