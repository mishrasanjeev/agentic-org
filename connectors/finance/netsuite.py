"""NetSuite connector — real NetSuite SuiteTalk REST API integration."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class NetsuiteConnector(BaseConnector):
    """NetSuite ERP connector using SuiteTalk REST Web Services.

    Requires config keys:
        account_id   — NetSuite account ID (e.g. "TSTDRV1234567")
        access_token — Pre-generated REST token (Token-Based Auth)

    Optional config overrides:
        base_url — Full base URL if the default template is not suitable
    """

    name = "netsuite"
    category = "finance"
    auth_type = "token"
    base_url = ""  # built dynamically from account_id
    rate_limit_rpm = 200

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._account_id: str = self.config.get("account_id", "")
        if not self.base_url and self._account_id:
            # NetSuite REST API base: account ID with underscores replacing dots
            safe_id = self._account_id.lower().replace(".", "_")
            self.base_url = (
                f"https://{safe_id}.suitetalk.api.netsuite.com"
                f"/services/rest/record/v1"
            )

    # ── Tool registration ──────────────────────────────────────────────

    def _register_tools(self):
        self._tool_registry["create_invoice"] = self.create_invoice
        self._tool_registry["get_invoice"] = self.get_invoice
        self._tool_registry["create_journal_entry"] = self.create_journal_entry
        self._tool_registry["get_account_balance"] = self.get_account_balance
        self._tool_registry["create_vendor_bill"] = self.create_vendor_bill
        self._tool_registry["create_purchase_order"] = self.create_purchase_order
        self._tool_registry["get_trial_balance"] = self.get_trial_balance
        self._tool_registry["search_records"] = self.search_records

    # ── Authentication ─────────────────────────────────────────────────

    async def _authenticate(self):
        """Authenticate using a pre-generated REST token (Bearer).

        NetSuite Token-Based Authentication (TBA) ultimately produces
        a bearer token that can be used for REST API calls.  The platform
        injects the token via config / GCP Secret Manager.
        """
        access_token = self._get_secret("access_token")
        if access_token:
            self._auth_headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Prefer": "transient",  # NetSuite: avoid stored searches
            }

    # ── Execute with 401 retry ─────────────────────────────────────────

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute tool with automatic 401 retry (re-authenticates and retries once)."""
        try:
            return await super().execute_tool(tool_name, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            logger.info("netsuite_401_retry", tool=tool_name)
            await self._authenticate()
            if self._client:
                await self._client.aclose()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_ms / 1000,
                headers=self._auth_headers,
            )
            return await super().execute_tool(tool_name, params)

    # ── Health check ───────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity by fetching a single account record."""
        try:
            data = await self._get("/account", params={"limit": 1})
            return {
                "status": "healthy",
                "total_results": data.get("totalResults", 0),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Invoices ───────────────────────────────────────────────────────

    async def create_invoice(self, **params) -> dict[str, Any]:
        """Create an invoice in NetSuite.

        Required: entity (customer internal ID), item (list of line items).
        Optional: tranDate (YYYY-MM-DD), memo, department.

        Example item line:
            {"item": {"id": "42"}, "quantity": 2, "rate": 150.00}
        """
        entity = params.get("entity")
        if not entity:
            return {"error": "entity (customer internal ID) is required"}
        items = params.get("item") or params.get("items")
        if not items:
            return {"error": "item (line items) is required"}

        body: dict[str, Any] = {
            "entity": {"id": str(entity)} if isinstance(entity, (str, int)) else entity,
            "item": {"items": items if isinstance(items, list) else [items]},
        }
        if params.get("tranDate"):
            body["tranDate"] = params["tranDate"]
        if params.get("memo"):
            body["memo"] = params["memo"]
        if params.get("department"):
            body["department"] = {"id": str(params["department"])}

        return await self._post("/invoice", body)

    async def get_invoice(self, **params) -> dict[str, Any]:
        """Get an invoice by internal ID.

        Required: id (invoice internal ID).
        Optional: expandSubResources (bool).
        """
        invoice_id = params.get("id") or params.get("invoice_id")
        if not invoice_id:
            return {"error": "id is required"}
        qp: dict[str, Any] = {}
        if params.get("expandSubResources"):
            qp["expandSubResources"] = "true"
        return await self._get(f"/invoice/{invoice_id}", params=qp or None)

    # ── Journal Entries ────────────────────────────────────────────────

    async def create_journal_entry(self, **params) -> dict[str, Any]:
        """Create a journal entry in NetSuite.

        Required: subsidiary (internal ID), line (debit/credit lines).
        Optional: tranDate (YYYY-MM-DD), memo.

        Example line:
            {"items": [
                {"account": {"id": "1"}, "debit": 1000.00, "memo": "Revenue"},
                {"account": {"id": "2"}, "credit": 1000.00, "memo": "Cash"}
            ]}
        """
        subsidiary = params.get("subsidiary")
        if not subsidiary:
            return {"error": "subsidiary is required"}
        lines = params.get("line") or params.get("lines")
        if not lines:
            return {"error": "line (debit/credit lines) is required"}

        body: dict[str, Any] = {
            "subsidiary": {"id": str(subsidiary)} if isinstance(subsidiary, (str, int)) else subsidiary,
            "line": {"items": lines if isinstance(lines, list) else [lines]},
        }
        if params.get("tranDate"):
            body["tranDate"] = params["tranDate"]
        if params.get("memo"):
            body["memo"] = params["memo"]

        return await self._post("/journalEntry", body)

    # ── Accounts ───────────────────────────────────────────────────────

    async def get_account_balance(self, **params) -> dict[str, Any]:
        """Get account details including balance.

        Required: id (account internal ID).
        """
        account_id = params.get("id") or params.get("account_id")
        if not account_id:
            return {"error": "id is required"}
        return await self._get(
            f"/account/{account_id}",
            params={"expand": "balance"},
        )

    # ── Vendor Bills ───────────────────────────────────────────────────

    async def create_vendor_bill(self, **params) -> dict[str, Any]:
        """Create a vendor bill (accounts payable) in NetSuite.

        Required: entity (vendor internal ID), item (line items).
        Optional: tranDate (YYYY-MM-DD), memo.

        Example item line:
            {"item": {"id": "55"}, "quantity": 1, "rate": 500.00}
        """
        entity = params.get("entity")
        if not entity:
            return {"error": "entity (vendor internal ID) is required"}
        items = params.get("item") or params.get("items")
        if not items:
            return {"error": "item (line items) is required"}

        body: dict[str, Any] = {
            "entity": {"id": str(entity)} if isinstance(entity, (str, int)) else entity,
            "item": {"items": items if isinstance(items, list) else [items]},
        }
        if params.get("tranDate"):
            body["tranDate"] = params["tranDate"]
        if params.get("memo"):
            body["memo"] = params["memo"]

        return await self._post("/vendorBill", body)

    # ── Purchase Orders ────────────────────────────────────────────────

    async def create_purchase_order(self, **params) -> dict[str, Any]:
        """Create a purchase order in NetSuite.

        Required: entity (vendor internal ID), item (line items).
        Optional: tranDate (YYYY-MM-DD), memo, shipTo.

        Example item line:
            {"item": {"id": "77"}, "quantity": 10, "rate": 25.00}
        """
        entity = params.get("entity")
        if not entity:
            return {"error": "entity (vendor internal ID) is required"}
        items = params.get("item") or params.get("items")
        if not items:
            return {"error": "item (line items) is required"}

        body: dict[str, Any] = {
            "entity": {"id": str(entity)} if isinstance(entity, (str, int)) else entity,
            "item": {"items": items if isinstance(items, list) else [items]},
        }
        if params.get("tranDate"):
            body["tranDate"] = params["tranDate"]
        if params.get("memo"):
            body["memo"] = params["memo"]
        if params.get("shipTo"):
            body["shipTo"] = {"id": str(params["shipTo"])}

        return await self._post("/purchaseOrder", body)

    # ── Trial Balance ──────────────────────────────────────────────────

    async def get_trial_balance(self, **params) -> dict[str, Any]:
        """Get trial balance (all accounts with balances for a period).

        Optional: period (accounting period internal ID), subsidiary.
        Returns list of accounts from the /account endpoint.
        """
        qp: dict[str, Any] = {"expand": "balance"}
        if params.get("period"):
            qp["period"] = params["period"]
        if params.get("subsidiary"):
            qp["subsidiary"] = params["subsidiary"]
        data = await self._get("/account", params=qp)
        items = data.get("items", [])
        return {
            "accounts": [
                {
                    "id": a.get("id"),
                    "acctName": a.get("acctName"),
                    "acctNumber": a.get("acctNumber"),
                    "acctType": a.get("acctType", {}).get("refName", ""),
                    "balance": a.get("balance"),
                }
                for a in items
            ],
            "total_results": data.get("totalResults", len(items)),
        }

    # ── Generic Search ─────────────────────────────────────────────────

    async def search_records(self, **params) -> dict[str, Any]:
        """Search NetSuite records using SuiteQL or record type browsing.

        Required: record_type (e.g. "invoice", "customer", "transaction").
        Optional: q (SuiteQL WHERE clause or search query),
                  limit (default 100), offset (default 0).

        Example:
            record_type="transaction", q="type='CustInvc' AND status='open'"
        """
        record_type = params.get("record_type", "")
        if not record_type:
            return {"error": "record_type is required"}

        qp: dict[str, Any] = {}
        if params.get("q"):
            qp["q"] = params["q"]
        if params.get("limit"):
            qp["limit"] = params["limit"]
        if params.get("offset"):
            qp["offset"] = params["offset"]

        data = await self._get(f"/{record_type}", params=qp or None)
        return {
            "records": data.get("items", []),
            "total_results": data.get("totalResults", 0),
            "has_more": data.get("hasMore", False),
        }
