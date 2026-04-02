"""Oracle Fusion Cloud connector — real Oracle Fusion REST API integration."""

from __future__ import annotations

import base64
from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()

# Oracle Fusion Cloud REST API version
_API_VERSION = "11.13.18.05"


class OracleFusionConnector(BaseConnector):
    name = "oracle_fusion"
    category = "finance"
    auth_type = "basic"
    base_url = "https://org.oraclecloud.com/fscmRestApi/resources/11.13.18.05"
    rate_limit_rpm = 500

    def __init__(self, config: dict[str, Any] | None = None):
        # Build instance-specific base_url before super().__init__ reads it
        if config and "instance" in config and "base_url" not in config:
            instance = config["instance"]
            config = dict(config)
            config["base_url"] = (
                f"https://{instance}.oraclecloud.com/fscmRestApi/resources/{_API_VERSION}"
            )
        super().__init__(config)

    def _register_tools(self):
        self._tool_registry["post_journal_entry"] = self.post_journal_entry
        self._tool_registry["get_gl_balance"] = self.get_gl_balance
        self._tool_registry["create_ap_invoice"] = self.create_ap_invoice
        self._tool_registry["approve_payment"] = self.approve_payment
        self._tool_registry["get_budget"] = self.get_budget
        self._tool_registry["create_po"] = self.create_po
        self._tool_registry["get_trial_balance"] = self.get_trial_balance
        self._tool_registry["run_period_close"] = self.run_period_close
        self._tool_registry["get_cash_position"] = self.get_cash_position
        self._tool_registry["run_reconciliation"] = self.run_reconciliation

    async def _authenticate(self):
        """Basic auth (username:password base64) for Oracle Fusion Cloud REST."""
        username = self._get_secret("username")
        password = self._get_secret("password")
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._auth_headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "REST-Framework-Version": "1",
        }

    def _unwrap(self, data: dict[str, Any]) -> dict[str, Any]:
        """Unwrap Oracle Fusion REST response.

        Oracle returns {"items": [...], "count": N, "hasMore": bool} for collections,
        or a direct object for single resources.
        """
        if not isinstance(data, dict):
            return data
        if "items" in data:
            return {
                "items": data["items"],
                "count": data.get("count", len(data["items"])),
                "has_more": data.get("hasMore", False),
            }
        return data

    def _unwrap_single(self, data: dict[str, Any]) -> dict[str, Any]:
        """Unwrap single-entity response (POST results)."""
        if not isinstance(data, dict):
            return data
        # Oracle may wrap in items for batch, or return flat object
        if "items" in data and isinstance(data["items"], list) and len(data["items"]) == 1:
            return data["items"][0]
        return data

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity by querying GL journals with limit=1."""
        try:
            data = await self._get("/generalLedgerJournals", params={"limit": 1})
            return {
                "status": "healthy",
                "count": data.get("count", 0),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── General Ledger ─────────────────────────────────────────────────

    async def post_journal_entry(self, **params) -> dict[str, Any]:
        """Post a journal entry to Oracle General Ledger.

        Required: LedgerName, AccountingDate, Status, JournalLines.

        JournalLines example:
            [{"AccountCombination": "101-00-1000-0000-000",
              "DebitAmount": 5000, "CreditAmount": 0, "Description": "Office supplies"}]
        """
        for required in ("LedgerName", "AccountingDate", "JournalLines"):
            if not params.get(required):
                return {"error": f"{required} is required"}

        body: dict[str, Any] = {
            "LedgerName": params["LedgerName"],
            "AccountingDate": params["AccountingDate"],
            "Status": params.get("Status", "NEW"),
            "JournalLines": params["JournalLines"],
        }
        # Pass through optional fields
        for field in ("JournalBatchName", "JournalName", "Description", "Currency"):
            if params.get(field):
                body[field] = params[field]

        data = await self._post("/generalLedgerJournals", body)
        return self._unwrap_single(data)

    async def get_gl_balance(self, **params) -> dict[str, Any]:
        """Query general ledger balances.

        Optional: q (filter expression), fields, limit.
        Example q: "LedgerName='US Primary Ledger';Period='Jan-26'"
        """
        qp: dict[str, Any] = {}
        if params.get("q"):
            qp["q"] = params["q"]
        if params.get("fields"):
            qp["fields"] = params["fields"]
        if params.get("limit"):
            qp["limit"] = params["limit"]
        data = await self._get("/generalLedgerBalances", params=qp if qp else None)
        return self._unwrap(data)

    # ── Accounts Payable ───────────────────────────────────────────────

    async def create_ap_invoice(self, **params) -> dict[str, Any]:
        """Create an AP (payables) invoice.

        Required: InvoiceNumber, InvoiceDate, Supplier, InvoiceAmount.
        """
        for required in ("InvoiceNumber", "InvoiceDate", "Supplier", "InvoiceAmount"):
            if not params.get(required):
                return {"error": f"{required} is required"}

        body: dict[str, Any] = {
            "InvoiceNumber": params["InvoiceNumber"],
            "InvoiceDate": params["InvoiceDate"],
            "Supplier": params["Supplier"],
            "InvoiceAmount": params["InvoiceAmount"],
        }
        for field in ("InvoiceCurrency", "Description", "PaymentTerms", "InvoiceLines"):
            if params.get(field):
                body[field] = params[field]

        data = await self._post("/payablesInvoices", body)
        return self._unwrap_single(data)

    # ── Payments ───────────────────────────────────────────────────────

    async def approve_payment(self, **params) -> dict[str, Any]:
        """Submit a payment request for approval.

        Required: PaymentRequestNumber, PaymentMethod.
        """
        for required in ("PaymentRequestNumber", "PaymentMethod"):
            if not params.get(required):
                return {"error": f"{required} is required"}

        body: dict[str, Any] = {
            "PaymentRequestNumber": params["PaymentRequestNumber"],
            "PaymentMethod": params["PaymentMethod"],
        }
        for field in ("PaymentDate", "Amount", "Supplier", "BankAccount"):
            if params.get(field):
                body[field] = params[field]

        data = await self._post("/paymentRequests", body)
        return self._unwrap_single(data)

    # ── Budgets ────────────────────────────────────────────────────────

    async def get_budget(self, **params) -> dict[str, Any]:
        """Query budget balances.

        Optional: q (filter), fields.
        Example q: "BudgetName='FY26 OpEx';Period='Q1-26'"
        """
        qp: dict[str, Any] = {}
        if params.get("q"):
            qp["q"] = params["q"]
        if params.get("fields"):
            qp["fields"] = params["fields"]
        data = await self._get("/budgetBalances", params=qp if qp else None)
        return self._unwrap(data)

    # ── Procurement ────────────────────────────────────────────────────

    async def create_po(self, **params) -> dict[str, Any]:
        """Create a purchase order.

        Required: OrderNumber, Supplier, Lines.

        Lines example:
            [{"LineNumber": 1, "ItemDescription": "Laptops", "Quantity": 10, "UnitPrice": 800}]
        """
        for required in ("OrderNumber", "Supplier", "Lines"):
            if not params.get(required):
                return {"error": f"{required} is required"}

        body: dict[str, Any] = {
            "OrderNumber": params["OrderNumber"],
            "Supplier": params["Supplier"],
            "Lines": params["Lines"],
        }
        for field in ("Description", "Currency", "BuyerEmail"):
            if params.get(field):
                body[field] = params[field]

        data = await self._post("/purchaseOrders", body)
        return self._unwrap_single(data)

    # ── Trial Balance ──────────────────────────────────────────────────

    async def get_trial_balance(self, **params) -> dict[str, Any]:
        """Query trial balance data.

        Optional: q (filter), limit.
        Example q: "LedgerName='US Primary Ledger';Period='Mar-26'"
        """
        qp: dict[str, Any] = {}
        if params.get("q"):
            qp["q"] = params["q"]
        if params.get("limit"):
            qp["limit"] = params["limit"]
        data = await self._get("/trialBalances", params=qp if qp else None)
        return self._unwrap(data)

    # ── Period Close ───────────────────────────────────────────────────

    async def run_period_close(self, **params) -> dict[str, Any]:
        """Run a period close action (open, close, reopen).

        Required: LedgerName, Period, Action.
        Action values: OPEN, CLOSE, REOPEN.
        """
        for required in ("LedgerName", "Period", "Action"):
            if not params.get(required):
                return {"error": f"{required} is required"}

        body: dict[str, Any] = {
            "LedgerName": params["LedgerName"],
            "Period": params["Period"],
            "Action": params["Action"],
        }
        data = await self._post("/periodCloseActions", body)
        return self._unwrap_single(data)

    # ── Cash Position ──────────────────────────────────────────────────

    async def get_cash_position(self, **params) -> dict[str, Any]:
        """Query cash positions.

        Optional: q (filter), fields.
        """
        qp: dict[str, Any] = {}
        if params.get("q"):
            qp["q"] = params["q"]
        if params.get("fields"):
            qp["fields"] = params["fields"]
        data = await self._get("/cashPositions", params=qp if qp else None)
        return self._unwrap(data)

    # ── Reconciliation ─────────────────────────────────────────────────

    async def run_reconciliation(self, **params) -> dict[str, Any]:
        """Run a reconciliation process.

        Required: ReconciliationType, Period.
        """
        for required in ("ReconciliationType", "Period"):
            if not params.get(required):
                return {"error": f"{required} is required"}

        body: dict[str, Any] = {
            "ReconciliationType": params["ReconciliationType"],
            "Period": params["Period"],
        }
        for field in ("LedgerName", "AccountSegment"):
            if params.get(field):
                body[field] = params[field]

        data = await self._post("/reconciliationProcesses", body)
        return self._unwrap_single(data)
