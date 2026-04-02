"""Zoho Books connector — real Zoho Books API v3 integration."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()

# Zoho API regions
_ZOHO_GLOBAL_BASE = "https://www.zohoapis.com/books/v3"
_ZOHO_IN_BASE = "https://books.zoho.in/api/v3"
_ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"


class ZohoBooksConnector(BaseConnector):
    name = "zoho_books"
    category = "finance"
    auth_type = "oauth2"
    base_url = _ZOHO_GLOBAL_BASE
    rate_limit_rpm = 100

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        # India-specific base URL if configured
        region = self.config.get("region", "global")
        if region == "in" and "base_url" not in self.config:
            self.base_url = _ZOHO_IN_BASE
        self._org_id: str = self.config.get("organization_id", "")

    def _register_tools(self):
        self._tool_registry["create_invoice"] = self.create_invoice
        self._tool_registry["list_invoices"] = self.list_invoices
        self._tool_registry["record_expense"] = self.record_expense
        self._tool_registry["get_balance_sheet"] = self.get_balance_sheet
        self._tool_registry["get_profit_loss"] = self.get_profit_loss
        self._tool_registry["list_chartofaccounts"] = self.list_chartofaccounts
        self._tool_registry["reconcile_transaction"] = self.reconcile_transaction

    async def _authenticate(self):
        """OAuth2 refresh token flow for Zoho Books."""
        refresh_token = self._get_secret("refresh_token")
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")

        if refresh_token and client_id:
            fresh_token = await self._refresh_oauth(client_id, client_secret, refresh_token)
            if fresh_token:
                self._auth_headers = {
                    "Authorization": f"Zoho-oauthtoken {fresh_token}",
                    "Content-Type": "application/json",
                }
                return

        # Fallback: stored access_token
        access_token = self._get_secret("access_token")
        if access_token:
            self._auth_headers = {
                "Authorization": f"Zoho-oauthtoken {access_token}",
                "Content-Type": "application/json",
            }

    async def _refresh_oauth(
        self, client_id: str, client_secret: str, refresh_token: str
    ) -> str | None:
        """Exchange refresh_token for a fresh access_token via Zoho OAuth2."""
        token_url = self.config.get("token_url", _ZOHO_TOKEN_URL)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    token_url,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info("zoho_books_token_refreshed", expires_in=data.get("expires_in"))
                return data["access_token"]
        except Exception as exc:
            logger.warning("zoho_books_token_refresh_failed", error=str(exc))
            return None

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute tool with automatic 401 retry (re-authenticates and retries once)."""
        try:
            return await super().execute_tool(tool_name, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            logger.info("zoho_books_401_retry", tool=tool_name)
            await self._authenticate()
            if self._client:
                await self._client.aclose()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_ms / 1000,
                headers=self._auth_headers,
            )
            return await super().execute_tool(tool_name, params)

    def _org_params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build query params with organization_id always included."""
        params: dict[str, Any] = {"organization_id": self._org_id}
        if extra:
            params.update({k: v for k, v in extra.items() if v is not None})
        return params

    def _unwrap(self, data: dict[str, Any], key: str | None = None) -> dict[str, Any]:
        """Unwrap Zoho response envelope: {"code": 0, "message": "success", "<key>": {...}}.

        Returns the inner object for the given key, or strips code/message from the dict.
        """
        if not isinstance(data, dict):
            return data
        if key and key in data:
            return data[key]
        # Auto-detect: return first value that is a dict or list (skip code/message)
        for k, v in data.items():
            if k not in ("code", "message") and isinstance(v, (dict, list)):
                return v
        return data

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity by listing organizations."""
        try:
            data = await self._get("/organizations")
            orgs = data.get("organizations", [])
            return {"status": "healthy", "organizations": len(orgs)}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Invoices ───────────────────────────────────────────────────────

    async def create_invoice(self, **params) -> dict[str, Any]:
        """Create a new invoice in Zoho Books.

        Required: customer_id, line_items (list of dicts with item_id, rate, quantity).
        Optional: date, due_date, gst_treatment, place_of_supply.
        """
        customer_id = params.get("customer_id")
        if not customer_id:
            return {"error": "customer_id is required"}
        line_items = params.get("line_items")
        if not line_items:
            return {"error": "line_items is required"}

        body: dict[str, Any] = {
            "customer_id": customer_id,
            "line_items": line_items,
        }
        for field in ("date", "due_date", "gst_treatment", "place_of_supply"):
            if params.get(field):
                body[field] = params[field]

        data = await self._post(
            "/invoices",
            data=body,
        )
        # Inject org_id as query param via raw request override
        return self._unwrap(data, "invoice")

    async def list_invoices(self, **params) -> dict[str, Any]:
        """List invoices with optional filters.

        Optional: status, date_start, date_end, customer_id, page.
        """
        qp: dict[str, Any] = {}
        if params.get("status"):
            qp["status"] = params["status"]
        if params.get("date_start"):
            qp["date_start"] = params["date_start"]
        if params.get("date_end"):
            qp["date_end"] = params["date_end"]
        if params.get("customer_id"):
            qp["customer_id"] = params["customer_id"]
        if params.get("page"):
            qp["page"] = params["page"]

        data = await self._get("/invoices", params=self._org_params(qp))
        invoices = self._unwrap(data, "invoices")
        return {
            "invoices": invoices if isinstance(invoices, list) else [],
            "page_context": data.get("page_context", {}),
        }

    # ── Expenses ───────────────────────────────────────────────────────

    async def record_expense(self, **params) -> dict[str, Any]:
        """Record an expense in Zoho Books.

        Required: account_id, amount, date.
        Optional: vendor_id, description.
        """
        for required in ("account_id", "amount", "date"):
            if not params.get(required):
                return {"error": f"{required} is required"}

        body: dict[str, Any] = {
            "account_id": params["account_id"],
            "amount": params["amount"],
            "date": params["date"],
        }
        for field in ("vendor_id", "description"):
            if params.get(field):
                body[field] = params[field]

        data = await self._post("/expenses", data=body)
        return self._unwrap(data, "expense")

    # ── Reports ────────────────────────────────────────────────────────

    async def get_balance_sheet(self, **params) -> dict[str, Any]:
        """Get balance sheet report.

        Optional: date (as_of_date, YYYY-MM-DD).
        """
        qp: dict[str, Any] = {}
        if params.get("date"):
            qp["date"] = params["date"]
        data = await self._get("/reports/balancesheet", params=self._org_params(qp))
        return self._unwrap(data, "balance_sheet")

    async def get_profit_loss(self, **params) -> dict[str, Any]:
        """Get profit and loss report.

        Optional: from_date, to_date (YYYY-MM-DD).
        """
        qp: dict[str, Any] = {}
        if params.get("from_date"):
            qp["from_date"] = params["from_date"]
        if params.get("to_date"):
            qp["to_date"] = params["to_date"]
        data = await self._get("/reports/profitandloss", params=self._org_params(qp))
        return self._unwrap(data, "profit_and_loss")

    # ── Chart of Accounts ──────────────────────────────────────────────

    async def list_chartofaccounts(self, **params) -> dict[str, Any]:
        """List chart of accounts.

        Optional: filter_by (e.g. AccountType.asset, AccountType.expense).
        """
        qp: dict[str, Any] = {}
        if params.get("filter_by"):
            qp["filter_by"] = params["filter_by"]
        data = await self._get("/chartofaccounts", params=self._org_params(qp))
        accounts = self._unwrap(data, "chartofaccounts")
        return {
            "chartofaccounts": accounts if isinstance(accounts, list) else [],
        }

    # ── Bank Reconciliation ────────────────────────────────────────────

    async def reconcile_transaction(self, **params) -> dict[str, Any]:
        """Reconcile an uncategorized bank transaction.

        Required: account_id, amount, date.
        Optional: reference_number.
        """
        for required in ("account_id", "amount", "date"):
            if not params.get(required):
                return {"error": f"{required} is required"}

        body: dict[str, Any] = {
            "account_id": params["account_id"],
            "amount": params["amount"],
            "date": params["date"],
        }
        if params.get("reference_number"):
            body["reference_number"] = params["reference_number"]

        data = await self._post("/banktransactions/uncategorized", data=body)
        return self._unwrap(data, "bank_transaction")

    # ── Internal: override _get/_post to inject organization_id ────────

    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """GET with organization_id always injected."""
        params = params or {}
        params.setdefault("organization_id", self._org_id)
        return await super()._get(path, params)

    async def _post(self, path: str, data: dict | None = None) -> dict[str, Any]:
        """POST with organization_id injected as query param.

        Zoho Books requires organization_id on the query string even for POST.
        """
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.post(
            path,
            json=data,
            params={"organization_id": self._org_id},
        )
        resp.raise_for_status()
        return resp.json()
