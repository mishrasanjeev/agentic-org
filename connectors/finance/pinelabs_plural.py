"""PineLabs Plural connector — Plural API v1 integration.

Uses OAuth2 bearer token authentication via /api/auth/v1/token.
All payment methods supported in redirect (hosted checkout) mode:
  CARD, UPI, NETBANKING, WALLET, CREDIT_EMI, DEBIT_EMI

Reference: https://developer.pinelabsonline.com/
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()

# Plural API base URLs
_BASE_URLS = {
    "sandbox": "https://pluraluat.v2.pinepg.in/api",
    "production": "https://api.pluralpay.in/api",
}

ALL_PAYMENT_METHODS = [
    "CARD", "UPI", "NETBANKING", "WALLET", "CREDIT_EMI", "DEBIT_EMI",
]


class PinelabsPluralConnector(BaseConnector):
    name = "pinelabs_plural"
    category = "finance"
    auth_type = "oauth2"
    base_url = "https://pluraluat.v2.pinepg.in/api"
    rate_limit_rpm = 200

    def _register_tools(self):
        self._tool_registry["create_order"] = self.create_order
        self._tool_registry["get_order_status"] = self.get_order_status
        self._tool_registry["create_payment_link"] = self.create_payment_link
        self._tool_registry["initiate_refund"] = self.initiate_refund
        self._tool_registry["get_settlement_report"] = self.get_settlement_report
        self._tool_registry["get_payout_analytics"] = self.get_payout_analytics

        # Defaults for pre-auth state (overwritten by _authenticate)
        self._access_token = ""
        self._token_expires = 0.0
        self._auth_headers = {"Content-Type": "application/json"}

    async def _authenticate(self):
        """Obtain OAuth2 bearer token from Plural auth endpoint."""
        self._client_id = self._get_secret("client_id")
        self._client_secret = self._get_secret("client_secret")
        self._merchant_id = self._get_secret("merchant_id")
        env = self._get_secret("environment") or "sandbox"
        self.base_url = _BASE_URLS.get(env, _BASE_URLS["sandbox"])

        await self._refresh_token()

    async def _refresh_token(self):
        """Fetch a new bearer token from /api/auth/v1/token."""
        if not self._client:
            raise RuntimeError("Connector not connected")

        resp = await self._client.post(
            f"{self.base_url}/auth/v1/token",
            json={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        expires_at = data.get("expires_at", "")
        if expires_at:
            try:
                dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                self._token_expires = dt.timestamp()
            except (ValueError, TypeError):
                self._token_expires = time.time() + 3000
        else:
            self._token_expires = time.time() + 3000

        self._auth_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }

    def _request_headers(self) -> dict[str, str]:
        """Build per-request headers with timestamp and request ID."""
        return {
            **self._auth_headers,
            "Request-Timestamp": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "Request-ID": str(uuid.uuid4()),
        }

    async def _ensure_token(self):
        """Refresh token if expired or about to expire."""
        if time.time() > (self._token_expires - 60):
            await self._refresh_token()

    async def health_check(self) -> dict[str, Any]:
        """Verify credentials by fetching a fresh token."""
        try:
            await self._refresh_token()
            return {"status": "healthy", "token_acquired": True}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Create Order (Hosted Checkout / Redirect) ───────────────────

    async def create_order(self, **params) -> dict[str, Any]:
        """Create a payment order — returns challenge_url for redirect checkout.

        Required: merchant_order_reference, amount (in paise).
        Optional: callback_url, failure_callback_url, customer_email,
                  allowed_payment_methods (defaults to all).
        """
        await self._ensure_token()
        if not self._client:
            raise RuntimeError("Connector not connected")

        merchant_ref = params.get("merchant_order_reference")
        amount = params.get("amount")
        if not merchant_ref or not amount:
            return {"error": "merchant_order_reference and amount are required"}

        body: dict[str, Any] = {
            "merchant_order_reference": merchant_ref,
            "order_amount": {
                "value": int(amount),
                "currency": params.get("currency", "INR"),
            },
            "pre_auth": params.get("pre_auth", False),
            "allowed_payment_methods": params.get(
                "allowed_payment_methods", ALL_PAYMENT_METHODS
            ),
        }

        if params.get("callback_url"):
            body["callback_url"] = params["callback_url"]
        if params.get("failure_callback_url"):
            body["failure_callback_url"] = params["failure_callback_url"]
        if params.get("notes"):
            body["notes"] = params["notes"]

        # Customer details
        if params.get("customer_email") or params.get("customer_name"):
            customer: dict[str, Any] = {}
            if params.get("customer_email"):
                customer["email_id"] = params["customer_email"]
            if params.get("customer_name"):
                parts = params["customer_name"].split(" ", 1)
                customer["first_name"] = parts[0]
                if len(parts) > 1:
                    customer["last_name"] = parts[1]
            if params.get("customer_phone"):
                customer["mobile_number"] = params["customer_phone"]
                customer["country_code"] = "91"
            body["purchase_details"] = {"customer": customer}

        # Use the hosted checkout endpoint (returns redirect_url)
        resp = await self._client.post(
            f"{self.base_url}/checkout/v1/orders",
            json=body,
            headers=self._request_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "order_id": data.get("order_id"),
            "redirect_url": data.get("redirect_url"),
            "status": "CREATED",
            "merchant_order_reference": merchant_ref,
            "integration_mode": "REDIRECT",
            "allowed_payment_methods": ALL_PAYMENT_METHODS,
        }

    # ── Get Order Status ────────────────────────────────────────────

    async def get_order_status(self, **params) -> dict[str, Any]:
        """Check the status of a payment order.

        Required: order_id.
        """
        await self._ensure_token()
        if not self._client:
            raise RuntimeError("Connector not connected")

        order_id = params.get("order_id")
        if not order_id:
            return {"error": "order_id is required"}

        resp = await self._client.get(
            f"{self.base_url}/pay/v1/orders/{order_id}",
            headers=self._request_headers(),
        )
        resp.raise_for_status()
        raw = resp.json()
        # Plural wraps response in "data" key
        data = raw.get("data", raw)

        return {
            "order_id": data.get("order_id"),
            "merchant_order_reference": data.get("merchant_order_reference"),
            "status": data.get("status"),
            "order_amount": data.get("order_amount", {}),
            "payments": data.get("payments", []),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }

    # ── Payment Links ───────────────────────────────────────────────

    async def create_payment_link(self, **params) -> dict[str, Any]:
        """Create a shareable payment link.

        Required: amount, merchant_order_reference.
        """
        await self._ensure_token()
        if not self._client:
            raise RuntimeError("Connector not connected")

        amount = params.get("amount")
        merchant_ref = params.get("merchant_order_reference")
        if not amount or not merchant_ref:
            return {"error": "amount and merchant_order_reference are required"}

        body: dict[str, Any] = {
            "merchant_order_reference": merchant_ref,
            "order_amount": {"value": int(amount), "currency": "INR"},
            "allowed_payment_methods": params.get(
                "allowed_payment_methods", ALL_PAYMENT_METHODS
            ),
        }

        if params.get("callback_url"):
            body["callback_url"] = params["callback_url"]

        resp = await self._client.post(
            f"{self.base_url}/pay/v1/orders",
            json=body,
            headers=self._request_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "order_id": data.get("order_id"),
            "challenge_url": data.get("challenge_url"),
            "status": data.get("status"),
        }

    # ── Refunds ─────────────────────────────────────────────────────

    async def initiate_refund(self, **params) -> dict[str, Any]:
        """Initiate a refund for a completed order.

        Required: order_id, amount (in paise).
        """
        await self._ensure_token()
        if not self._client:
            raise RuntimeError("Connector not connected")

        order_id = params.get("order_id")
        amount = params.get("amount")
        if not order_id or not amount:
            return {"error": "order_id and amount are required"}

        body = {
            "merchant_refund_reference": f"ref_{uuid.uuid4().hex[:12]}",
            "refund_amount": {"value": int(amount), "currency": "INR"},
        }

        resp = await self._client.post(
            f"{self.base_url}/pay/v1/orders/{order_id}/refunds",
            json=body,
            headers=self._request_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "refund_id": data.get("refund_id"),
            "order_id": order_id,
            "status": data.get("status"),
            "refund_amount": data.get("refund_amount", {}),
        }

    # ── Settlements ─────────────────────────────────────────────────

    async def get_settlement_report(self, **params) -> dict[str, Any]:
        """Retrieve settlement details for a date range.

        Optional: start_date, end_date (ISO 8601).
        """
        await self._ensure_token()
        if not self._client:
            raise RuntimeError("Connector not connected")

        query: dict[str, Any] = {}
        if params.get("start_date"):
            query["start_date"] = params["start_date"]
        if params.get("end_date"):
            query["end_date"] = params["end_date"]

        resp = await self._client.get(
            f"{self.base_url}/pay/v1/settlements",
            params=query,
            headers=self._request_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ── Analytics ───────────────────────────────────────────────────

    async def get_payout_analytics(self, **params) -> dict[str, Any]:
        """Retrieve payout analytics for a date range.

        Optional: start_date, end_date (ISO 8601).
        """
        await self._ensure_token()
        if not self._client:
            raise RuntimeError("Connector not connected")

        query: dict[str, Any] = {}
        if params.get("start_date"):
            query["start_date"] = params["start_date"]
        if params.get("end_date"):
            query["end_date"] = params["end_date"]

        resp = await self._client.get(
            f"{self.base_url}/pay/v1/analytics/payouts",
            params=query,
            headers=self._request_headers(),
        )
        resp.raise_for_status()
        return resp.json()
