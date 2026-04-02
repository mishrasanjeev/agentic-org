"""Stripe connector — real Stripe API v1 integration.

Uses Bearer token auth and form-encoded POST bodies per Stripe's API spec.
Reference: https://stripe.com/docs/api
"""

from __future__ import annotations

from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class StripeConnector(BaseConnector):
    name = "stripe"
    category = "finance"
    auth_type = "api_key"
    base_url = "https://api.stripe.com"
    rate_limit_rpm = 300

    def _register_tools(self):
        self._tool_registry["create_payment_intent"] = self.create_payment_intent
        self._tool_registry["list_charges"] = self.list_charges
        self._tool_registry["create_payout"] = self.create_payout
        self._tool_registry["get_balance"] = self.get_balance
        self._tool_registry["list_invoices"] = self.list_invoices
        self._tool_registry["create_customer"] = self.create_customer
        self._tool_registry["list_disputes"] = self.list_disputes
        self._tool_registry["create_refund"] = self.create_refund

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

    async def health_check(self) -> dict[str, Any]:
        """Verify credentials by fetching the account balance."""
        try:
            data = await self._get("/v1/balance")
            available = data.get("available", [])
            return {
                "status": "healthy",
                "available_balance": [
                    {"amount": b["amount"], "currency": b["currency"]}
                    for b in available
                ],
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Payment Intents ────────────────────────────────────────────────

    async def create_payment_intent(self, **params) -> dict[str, Any]:
        """Create a PaymentIntent for a given amount and currency.

        Required params: amount (int, in smallest currency unit), currency.
        Optional: payment_method_types, description, metadata.
        """
        amount = params.get("amount")
        currency = params.get("currency", "usd")
        if not amount:
            return {"error": "amount is required"}

        form: dict[str, Any] = {
            "amount": amount,
            "currency": currency,
        }
        if params.get("payment_method_types"):
            for i, pmt in enumerate(params["payment_method_types"]):
                form[f"payment_method_types[{i}]"] = pmt
        if params.get("description"):
            form["description"] = params["description"]
        if params.get("metadata") and isinstance(params["metadata"], dict):
            for k, v in params["metadata"].items():
                form[f"metadata[{k}]"] = v

        data = await self._post_form("/v1/payment_intents", form)
        return {
            "id": data.get("id"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
            "status": data.get("status"),
            "client_secret": data.get("client_secret"),
            "created": data.get("created"),
        }

    # ── Charges ────────────────────────────────────────────────────────

    async def list_charges(self, **params) -> dict[str, Any]:
        """List charges with optional filters.

        Optional params: limit, created[gte] (unix timestamp), customer.
        """
        query: dict[str, Any] = {}
        if params.get("limit"):
            query["limit"] = params["limit"]
        if params.get("created[gte]") or params.get("created_gte"):
            query["created[gte]"] = params.get("created[gte]") or params["created_gte"]
        if params.get("customer"):
            query["customer"] = params["customer"]

        data = await self._get("/v1/charges", params=query)
        return {
            "charges": [
                {
                    "id": c["id"],
                    "amount": c.get("amount"),
                    "currency": c.get("currency"),
                    "status": c.get("status"),
                    "customer": c.get("customer"),
                    "description": c.get("description"),
                    "created": c.get("created"),
                }
                for c in data.get("data", [])
            ],
            "has_more": data.get("has_more", False),
        }

    # ── Payouts ────────────────────────────────────────────────────────

    async def create_payout(self, **params) -> dict[str, Any]:
        """Create a payout to the connected bank account.

        Required: amount (int, smallest currency unit).
        Optional: currency, description.
        """
        amount = params.get("amount")
        if not amount:
            return {"error": "amount is required"}

        form: dict[str, Any] = {"amount": amount}
        if params.get("currency"):
            form["currency"] = params["currency"]
        if params.get("description"):
            form["description"] = params["description"]

        data = await self._post_form("/v1/payouts", form)
        return {
            "id": data.get("id"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
            "status": data.get("status"),
            "arrival_date": data.get("arrival_date"),
            "created": data.get("created"),
        }

    # ── Balance ────────────────────────────────────────────────────────

    async def get_balance(self, **params) -> dict[str, Any]:
        """Retrieve the current account balance."""
        data = await self._get("/v1/balance")
        return {
            "available": [
                {"amount": b["amount"], "currency": b["currency"]}
                for b in data.get("available", [])
            ],
            "pending": [
                {"amount": b["amount"], "currency": b["currency"]}
                for b in data.get("pending", [])
            ],
            "livemode": data.get("livemode"),
        }

    # ── Invoices ───────────────────────────────────────────────────────

    async def list_invoices(self, **params) -> dict[str, Any]:
        """List invoices with optional filters.

        Optional params: limit, customer, status (draft|open|paid|void|uncollectible).
        """
        query: dict[str, Any] = {}
        if params.get("limit"):
            query["limit"] = params["limit"]
        if params.get("customer"):
            query["customer"] = params["customer"]
        if params.get("status"):
            query["status"] = params["status"]

        data = await self._get("/v1/invoices", params=query)
        return {
            "invoices": [
                {
                    "id": inv["id"],
                    "customer": inv.get("customer"),
                    "amount_due": inv.get("amount_due"),
                    "amount_paid": inv.get("amount_paid"),
                    "currency": inv.get("currency"),
                    "status": inv.get("status"),
                    "due_date": inv.get("due_date"),
                    "created": inv.get("created"),
                }
                for inv in data.get("data", [])
            ],
            "has_more": data.get("has_more", False),
        }

    # ── Customers ──────────────────────────────────────────────────────

    async def create_customer(self, **params) -> dict[str, Any]:
        """Create a new Stripe customer.

        Optional params: email, name, description, metadata.
        """
        form: dict[str, Any] = {}
        if params.get("email"):
            form["email"] = params["email"]
        if params.get("name"):
            form["name"] = params["name"]
        if params.get("description"):
            form["description"] = params["description"]
        if params.get("metadata") and isinstance(params["metadata"], dict):
            for k, v in params["metadata"].items():
                form[f"metadata[{k}]"] = v

        data = await self._post_form("/v1/customers", form)
        return {
            "id": data.get("id"),
            "email": data.get("email"),
            "name": data.get("name"),
            "description": data.get("description"),
            "created": data.get("created"),
            "livemode": data.get("livemode"),
        }

    # ── Disputes ───────────────────────────────────────────────────────

    async def list_disputes(self, **params) -> dict[str, Any]:
        """List disputes with optional filters.

        Optional params: limit, charge.
        """
        query: dict[str, Any] = {}
        if params.get("limit"):
            query["limit"] = params["limit"]
        if params.get("charge"):
            query["charge"] = params["charge"]

        data = await self._get("/v1/disputes", params=query)
        return {
            "disputes": [
                {
                    "id": d["id"],
                    "charge": d.get("charge"),
                    "amount": d.get("amount"),
                    "currency": d.get("currency"),
                    "status": d.get("status"),
                    "reason": d.get("reason"),
                    "created": d.get("created"),
                }
                for d in data.get("data", [])
            ],
            "has_more": data.get("has_more", False),
        }

    # ── Refunds ────────────────────────────────────────────────────────

    async def create_refund(self, **params) -> dict[str, Any]:
        """Create a refund for a PaymentIntent.

        Required: payment_intent.
        Optional: amount (partial refund), reason (duplicate|fraudulent|requested_by_customer).
        """
        payment_intent = params.get("payment_intent")
        if not payment_intent:
            return {"error": "payment_intent is required"}

        form: dict[str, Any] = {"payment_intent": payment_intent}
        if params.get("amount"):
            form["amount"] = params["amount"]
        if params.get("reason"):
            form["reason"] = params["reason"]

        data = await self._post_form("/v1/refunds", form)
        return {
            "id": data.get("id"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
            "status": data.get("status"),
            "payment_intent": data.get("payment_intent"),
            "reason": data.get("reason"),
            "created": data.get("created"),
        }
