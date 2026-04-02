"""PineLabs Plural connector — real PhonePe/Plural PG API integration.

Uses X-VERIFY header with SHA256 checksum authentication.
Reference: https://developer.phonepe.com/docs/
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class PinelabsPluralConnector(BaseConnector):
    name = "pinelabs_plural"
    category = "finance"
    auth_type = "api_key"
    base_url = "https://api.pluralonline.com/api"
    rate_limit_rpm = 200

    def _register_tools(self):
        self._tool_registry["create_order"] = self.create_order
        self._tool_registry["check_order_status"] = self.check_order_status
        self._tool_registry["create_payment_link"] = self.create_payment_link
        self._tool_registry["initiate_refund"] = self.initiate_refund
        self._tool_registry["get_settlement_report"] = self.get_settlement_report
        self._tool_registry["get_payout_analytics"] = self.get_payout_analytics

    async def _authenticate(self):
        """Load merchant credentials for X-VERIFY signing.

        PineLabs Plural uses per-request checksum auth rather than a static
        bearer token.  The salt_key and salt_index are stored and used by
        ``_sign_request`` on every outbound call.
        """
        self._merchant_id = self._get_secret("merchant_id")
        self._salt_key = self._get_secret("salt_key")
        self._salt_index = self._get_secret("salt_index") or "1"
        self._auth_headers = {"Content-Type": "application/json"}

    def _sign_request(self, payload_json: str, endpoint: str) -> str:
        """Compute the X-VERIFY header value.

        checksum = SHA256(base64(payload) + endpoint + salt_key) + "###" + salt_index
        """
        import base64

        encoded_payload = base64.b64encode(payload_json.encode()).decode()
        raw = f"{encoded_payload}{endpoint}{self._salt_key}"
        sha = hashlib.sha256(raw.encode()).hexdigest()
        return f"{sha}###{self._salt_index}"

    async def _signed_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST with X-VERIFY checksum header."""
        if not self._client:
            raise RuntimeError("Connector not connected")
        payload_json = json.dumps(body)
        x_verify = self._sign_request(payload_json, path)
        resp = await self._client.post(
            path,
            content=payload_json,
            headers={
                "Content-Type": "application/json",
                "X-VERIFY": x_verify,
                "X-MERCHANT-ID": self._merchant_id,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def _signed_get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """GET with X-VERIFY checksum header (payload is empty for GETs)."""
        if not self._client:
            raise RuntimeError("Connector not connected")
        x_verify = self._sign_request("", path)
        resp = await self._client.get(
            path,
            params=params,
            headers={
                "X-VERIFY": x_verify,
                "X-MERCHANT-ID": self._merchant_id,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def health_check(self) -> dict[str, Any]:
        """Verify credentials by checking status of a dummy transaction."""
        try:
            data = await self._signed_get(
                f"/pg/v1/status/{self._merchant_id}/health_ping"
            )
            return {
                "status": "healthy",
                "response_code": data.get("code"),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Order / Payment ────────────────────────────────────────────────

    async def create_order(self, **params) -> dict[str, Any]:
        """Initiate a payment order via the Plural PG.

        Required: merchantTransactionId, amount (in paise), merchantUserId,
                  redirectUrl, redirectMode, paymentInstrument.
        """
        merchant_txn_id = params.get("merchantTransactionId")
        amount = params.get("amount")
        if not merchant_txn_id or not amount:
            return {"error": "merchantTransactionId and amount are required"}

        body = {
            "merchantId": params.get("merchantId") or self._merchant_id,
            "merchantTransactionId": merchant_txn_id,
            "amount": amount,
            "merchantUserId": params.get("merchantUserId", ""),
            "redirectUrl": params.get("redirectUrl", ""),
            "redirectMode": params.get("redirectMode", "REDIRECT"),
            "paymentInstrument": params.get("paymentInstrument", {"type": "PAY_PAGE"}),
        }

        data = await self._signed_post("/pg/v1/pay", body)
        inner = data.get("data", {})
        return {
            "success": data.get("success"),
            "code": data.get("code"),
            "message": data.get("message"),
            "merchantTransactionId": inner.get("merchantTransactionId"),
            "instrumentResponse": inner.get("instrumentResponse"),
        }

    async def check_order_status(self, **params) -> dict[str, Any]:
        """Check the status of a payment transaction.

        Required: merchantTransactionId.
        Optional: merchantId (defaults to configured merchant).
        """
        merchant_txn_id = params.get("merchantTransactionId")
        if not merchant_txn_id:
            return {"error": "merchantTransactionId is required"}
        merchant_id = params.get("merchantId") or self._merchant_id

        data = await self._signed_get(
            f"/pg/v1/status/{merchant_id}/{merchant_txn_id}"
        )
        inner = data.get("data", {})
        return {
            "success": data.get("success"),
            "code": data.get("code"),
            "message": data.get("message"),
            "merchantTransactionId": inner.get("merchantTransactionId"),
            "transactionId": inner.get("transactionId"),
            "amount": inner.get("amount"),
            "state": inner.get("state"),
            "paymentInstrument": inner.get("paymentInstrument"),
        }

    # ── Payment Links ──────────────────────────────────────────────────

    async def create_payment_link(self, **params) -> dict[str, Any]:
        """Create a shareable payment link.

        Required: amount, merchantTransactionId.
        Optional: merchantUserId.
        """
        amount = params.get("amount")
        merchant_txn_id = params.get("merchantTransactionId")
        if not amount or not merchant_txn_id:
            return {"error": "amount and merchantTransactionId are required"}

        body = {
            "merchantId": self._merchant_id,
            "merchantTransactionId": merchant_txn_id,
            "merchantUserId": params.get("merchantUserId", ""),
            "amount": amount,
        }

        data = await self._signed_post("/v1/payment-links/create", body)
        inner = data.get("data", {})
        return {
            "success": data.get("success"),
            "code": data.get("code"),
            "message": data.get("message"),
            "paymentLinkId": inner.get("paymentLinkId"),
            "paymentLinkUrl": inner.get("paymentLinkUrl"),
            "expiresAt": inner.get("expiresAt"),
        }

    # ── Refunds ────────────────────────────────────────────────────────

    async def initiate_refund(self, **params) -> dict[str, Any]:
        """Initiate a refund for a completed transaction.

        Required: merchantTransactionId, originalTransactionId, amount (in paise).
        """
        merchant_txn_id = params.get("merchantTransactionId")
        original_txn_id = params.get("originalTransactionId")
        amount = params.get("amount")
        if not merchant_txn_id or not original_txn_id or not amount:
            return {
                "error": "merchantTransactionId, originalTransactionId, and amount are required"
            }

        body = {
            "merchantId": params.get("merchantId") or self._merchant_id,
            "merchantTransactionId": merchant_txn_id,
            "originalTransactionId": original_txn_id,
            "amount": amount,
        }

        data = await self._signed_post("/pg/v1/refund", body)
        inner = data.get("data", {})
        return {
            "success": data.get("success"),
            "code": data.get("code"),
            "message": data.get("message"),
            "merchantTransactionId": inner.get("merchantTransactionId"),
            "transactionId": inner.get("transactionId"),
            "amount": inner.get("amount"),
            "state": inner.get("state"),
        }

    # ── Settlements ────────────────────────────────────────────────────

    async def get_settlement_report(self, **params) -> dict[str, Any]:
        """Retrieve settlement details for a date range.

        Optional: startDate, endDate (ISO 8601 format).
        """
        query: dict[str, Any] = {}
        if params.get("startDate"):
            query["startDate"] = params["startDate"]
        if params.get("endDate"):
            query["endDate"] = params["endDate"]

        data = await self._signed_get("/v1/settlements", params=query)
        settlements = data.get("data", [])
        if isinstance(settlements, dict):
            settlements = settlements.get("settlements", [])
        return {
            "success": data.get("success"),
            "code": data.get("code"),
            "settlements": [
                {
                    "settlementId": s.get("settlementId"),
                    "amount": s.get("amount"),
                    "status": s.get("status"),
                    "settledAt": s.get("settledAt"),
                    "utr": s.get("utr"),
                }
                for s in (settlements if isinstance(settlements, list) else [])
            ],
        }

    # ── Analytics ──────────────────────────────────────────────────────

    async def get_payout_analytics(self, **params) -> dict[str, Any]:
        """Retrieve payout analytics for a date range.

        Optional: startDate, endDate (ISO 8601 format).
        """
        query: dict[str, Any] = {}
        if params.get("startDate"):
            query["startDate"] = params["startDate"]
        if params.get("endDate"):
            query["endDate"] = params["endDate"]

        data = await self._signed_get("/v1/analytics/payouts", params=query)
        inner = data.get("data", {})
        return {
            "success": data.get("success"),
            "code": data.get("code"),
            "totalPayouts": inner.get("totalPayouts"),
            "totalAmount": inner.get("totalAmount"),
            "currency": inner.get("currency"),
            "payouts": inner.get("payouts", []),
        }
