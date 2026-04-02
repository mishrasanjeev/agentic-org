"""QuickBooks Online connector — real QuickBooks API v3 integration."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()

_QBO_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"


class QuickbooksConnector(BaseConnector):
    name = "quickbooks"
    category = "finance"
    auth_type = "oauth2"
    base_url = "https://quickbooks.api.intuit.com/v3"
    rate_limit_rpm = 500

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._realm_id: str = self.config.get("realm_id", "")

    def _register_tools(self):
        self._tool_registry["create_invoice"] = self.create_invoice
        self._tool_registry["record_payment"] = self.record_payment
        self._tool_registry["get_profit_loss"] = self.get_profit_loss
        self._tool_registry["get_balance_sheet"] = self.get_balance_sheet
        self._tool_registry["query"] = self.query
        self._tool_registry["get_company_info"] = self.get_company_info

    async def _authenticate(self):
        """OAuth2 refresh token flow for QuickBooks Online."""
        refresh_token = self._get_secret("refresh_token")
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")

        if refresh_token and client_id:
            fresh_token = await self._refresh_oauth(client_id, client_secret, refresh_token)
            if fresh_token:
                self._auth_headers = {
                    "Authorization": f"Bearer {fresh_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                return

        # Fallback: stored access_token
        access_token = self._get_secret("access_token")
        if access_token:
            self._auth_headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

    async def _refresh_oauth(
        self, client_id: str, client_secret: str, refresh_token: str
    ) -> str | None:
        """Exchange refresh_token for a fresh access_token via Intuit OAuth2."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    _QBO_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    auth=(client_id, client_secret),
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info("quickbooks_token_refreshed", expires_in=data.get("expires_in"))
                return data["access_token"]
        except Exception as exc:
            logger.warning("quickbooks_token_refresh_failed", error=str(exc))
            return None

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute tool with automatic 401 retry (re-authenticates and retries once)."""
        try:
            return await super().execute_tool(tool_name, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            logger.info("quickbooks_401_retry", tool=tool_name)
            await self._authenticate()
            if self._client:
                await self._client.aclose()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_ms / 1000,
                headers=self._auth_headers,
            )
            return await super().execute_tool(tool_name, params)

    def _company_path(self, suffix: str) -> str:
        """Build /company/{realmId}/... path."""
        return f"/company/{self._realm_id}/{suffix}"

    def _unwrap(self, data: dict[str, Any], key: str | None = None) -> dict[str, Any]:
        """Unwrap QuickBooks response envelope.

        QBO wraps responses as {"QueryResponse": {"Invoice": [...]}} or {"Invoice": {...}}.
        Returns the inner object.
        """
        if not isinstance(data, dict):
            return data

        # QueryResponse envelope (from query endpoint)
        if "QueryResponse" in data:
            qr = data["QueryResponse"]
            if isinstance(qr, dict):
                # Return the first list/dict value inside QueryResponse
                for v in qr.values():
                    if isinstance(v, (list, dict)):
                        return v
                return qr
            return qr

        # Direct entity envelope: {"Invoice": {...}} or {"Payment": {...}}
        if key and key in data:
            return data[key]

        # Auto-detect: return first dict/list value (skip time/metadata keys)
        for k, v in data.items():
            if isinstance(v, (dict, list)) and k != "time":
                return v

        return data

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity by fetching company info."""
        try:
            path = self._company_path(f"companyinfo/{self._realm_id}")
            data = await self._get(path)
            info = self._unwrap(data, "CompanyInfo")
            return {
                "status": "healthy",
                "company_name": info.get("CompanyName", "") if isinstance(info, dict) else "",
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Invoices ───────────────────────────────────────────────────────

    async def create_invoice(self, **params) -> dict[str, Any]:
        """Create an invoice in QuickBooks Online.

        Required: Line (list of line items), CustomerRef ({"value": "customer_id"}).
        Optional: TxnDate.

        Example Line item:
            {"Amount": 100.00, "DetailType": "SalesItemLineDetail",
             "SalesItemLineDetail": {"ItemRef": {"value": "1"}}}
        """
        lines = params.get("Line") or params.get("lines")
        customer_ref = params.get("CustomerRef") or params.get("customer_ref")
        if not lines:
            return {"error": "Line (line items) is required"}
        if not customer_ref:
            return {"error": "CustomerRef is required"}

        # Normalize customer_ref to QBO format
        if isinstance(customer_ref, str):
            customer_ref = {"value": customer_ref}

        body: dict[str, Any] = {
            "Line": lines,
            "CustomerRef": customer_ref,
        }
        if params.get("TxnDate") or params.get("txn_date"):
            body["TxnDate"] = params.get("TxnDate") or params["txn_date"]

        data = await self._post(self._company_path("invoice"), body)
        return self._unwrap(data, "Invoice")

    # ── Payments ───────────────────────────────────────────────────────

    async def record_payment(self, **params) -> dict[str, Any]:
        """Record a payment in QuickBooks Online.

        Required: TotalAmt, CustomerRef.
        Optional: PaymentMethodRef, DepositToAccountRef.
        """
        total_amt = params.get("TotalAmt") or params.get("total_amt")
        customer_ref = params.get("CustomerRef") or params.get("customer_ref")
        if total_amt is None:
            return {"error": "TotalAmt is required"}
        if not customer_ref:
            return {"error": "CustomerRef is required"}

        if isinstance(customer_ref, str):
            customer_ref = {"value": customer_ref}

        body: dict[str, Any] = {
            "TotalAmt": total_amt,
            "CustomerRef": customer_ref,
        }
        for field in ("PaymentMethodRef", "DepositToAccountRef"):
            if params.get(field):
                body[field] = params[field]

        data = await self._post(self._company_path("payment"), body)
        return self._unwrap(data, "Payment")

    # ── Reports ────────────────────────────────────────────────────────

    async def get_profit_loss(self, **params) -> dict[str, Any]:
        """Get Profit and Loss report.

        Optional: start_date, end_date (YYYY-MM-DD).
        """
        qp: dict[str, Any] = {}
        if params.get("start_date"):
            qp["start_date"] = params["start_date"]
        if params.get("end_date"):
            qp["end_date"] = params["end_date"]
        data = await self._get(
            self._company_path("reports/ProfitAndLoss"),
            params=qp if qp else None,
        )
        return data

    async def get_balance_sheet(self, **params) -> dict[str, Any]:
        """Get Balance Sheet report.

        Optional: date (as-of date, YYYY-MM-DD).
        """
        qp: dict[str, Any] = {}
        if params.get("date"):
            qp["date"] = params["date"]
        data = await self._get(
            self._company_path("reports/BalanceSheet"),
            params=qp if qp else None,
        )
        return data

    # ── Query ──────────────────────────────────────────────────────────

    async def query(self, **params) -> dict[str, Any]:
        """Execute a QuickBooks SQL-like query.

        Required: query (e.g. "SELECT * FROM Invoice WHERE TotalAmt > '100.00'").
        """
        sql = params.get("query", "")
        if not sql:
            return {"error": "query is required"}
        data = await self._get(
            self._company_path("query"),
            params={"query": sql},
        )
        return self._unwrap(data)

    # ── Company Info ───────────────────────────────────────────────────

    async def get_company_info(self, **params) -> dict[str, Any]:
        """Get company information for the connected QBO account."""
        data = await self._get(
            self._company_path(f"companyinfo/{self._realm_id}"),
        )
        return self._unwrap(data, "CompanyInfo")
