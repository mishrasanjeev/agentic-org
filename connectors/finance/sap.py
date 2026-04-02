"""SAP S/4HANA Cloud connector — real OData API v4 integration."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class SapConnector(BaseConnector):
    name = "sap"
    category = "finance"
    auth_type = "odata_oauth2"
    base_url = "https://localhost.s4hana.cloud.sap/sap/opu/odata/sap"
    rate_limit_rpm = 300

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        # Build the real base_url from the host config key
        host = config.get("host", "")
        if host:
            config.setdefault(
                "base_url",
                f"https://{host}.s4hana.cloud.sap/sap/opu/odata/sap",
            )
        super().__init__(config)

    def _register_tools(self):
        self._tool_registry["post_journal_entry"] = self.post_journal_entry
        self._tool_registry["get_gl_balance"] = self.get_gl_balance
        self._tool_registry["create_purchase_order"] = self.create_purchase_order
        self._tool_registry["post_goods_receipt"] = self.post_goods_receipt
        self._tool_registry["run_payment_run"] = self.run_payment_run
        self._tool_registry["get_vendor_master"] = self.get_vendor_master
        self._tool_registry["get_cost_center"] = self.get_cost_center

    async def _authenticate(self):
        """OAuth2 client_credentials flow via SAP XSUAA."""
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        subdomain = self.config.get("subdomain", "")
        region = self.config.get("region", "eu10")

        if not subdomain:
            # Fallback: allow explicit token_url in config
            token_url = self.config.get("token_url", "")
            if not token_url:
                raise ValueError(
                    "SAP connector requires config['subdomain'] or config['token_url']"
                )
        else:
            token_url = (
                f"https://{subdomain}.authentication.{region}.hana.ondemand.com"
                "/oauth/token"
            )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()
            token = data["access_token"]
            logger.info(
                "sap_token_acquired",
                expires_in=data.get("expires_in"),
                token_type=data.get("token_type"),
            )

        self._auth_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute tool with automatic 401 retry (re-authenticates and retries once)."""
        try:
            return await super().execute_tool(tool_name, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            logger.info("sap_401_retry", tool=tool_name)
            await self._authenticate()
            if self._client:
                await self._client.aclose()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_ms / 1000,
                headers=self._auth_headers,
            )
            return await super().execute_tool(tool_name, params)

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await self._odata_get(
                "/API_BUSINESS_PARTNER/A_BusinessPartner",
                params={"$top": "1"},
            )
            return {"status": "healthy", "sample": data}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Journal Entry ──────────────────────────────────────────────────

    async def post_journal_entry(self, **params) -> dict[str, Any]:
        """Post a financial journal entry (FI document) to SAP S/4HANA.

        Required params:
            company_code: SAP company code (e.g. "1000")
            document_date: Document date (YYYY-MM-DD)
            posting_date: Posting date (YYYY-MM-DD)
            items: list of line items, each with:
                - GLAccount, DebitCreditCode, AmountInCompanyCodeCurrency,
                  CompanyCodeCurrency
        """
        company_code = params.get("company_code", "")
        if not company_code:
            return {"error": "company_code is required"}
        items = params.get("items", [])
        if not items:
            return {"error": "items (line items) are required"}

        body = {
            "CompanyCode": company_code,
            "DocumentDate": params.get("document_date", ""),
            "PostingDate": params.get("posting_date", ""),
            "to_Item": items,
        }
        return await self._post("/API_JOURNAL_ENTRY_SRV/A_JournalEntry", body)

    # ── GL Account Balance ─────────────────────────────────────────────

    async def get_gl_balance(self, **params) -> dict[str, Any]:
        """Retrieve G/L account balances for a company code and fiscal year.

        Required params:
            company_code: SAP company code
            fiscal_year: Fiscal year (e.g. "2026")
        Optional params:
            gl_account: Specific G/L account number to filter
        """
        company_code = params.get("company_code", "")
        fiscal_year = params.get("fiscal_year", "")
        if not company_code or not fiscal_year:
            return {"error": "company_code and fiscal_year are required"}

        odata_filter = (
            f"CompanyCode eq '{company_code}' and FiscalYear eq '{fiscal_year}'"
        )
        gl_account = params.get("gl_account", "")
        if gl_account:
            odata_filter += f" and GLAccount eq '{gl_account}'"

        return await self._odata_get(
            "/API_GLACCOUNTBALANCE/A_GLAccountBalance",
            params={"$filter": odata_filter},
        )

    # ── Purchase Order ─────────────────────────────────────────────────

    async def create_purchase_order(self, **params) -> dict[str, Any]:
        """Create a purchase order in SAP S/4HANA.

        Required params:
            company_code: SAP company code
            purchase_order_type: PO type (e.g. "NB" for standard)
            supplier: Supplier (vendor) number
            items: list of PO items, each with:
                - PurchaseOrderItemText, Plant, OrderQuantity,
                  PurchaseOrderQuantityUnit, NetPriceAmount, Material
        """
        company_code = params.get("company_code", "")
        supplier = params.get("supplier", "")
        if not company_code or not supplier:
            return {"error": "company_code and supplier are required"}

        items = params.get("items", [])
        if not items:
            return {"error": "items (PO line items) are required"}

        body = {
            "CompanyCode": company_code,
            "PurchaseOrderType": params.get("purchase_order_type", "NB"),
            "Supplier": supplier,
            "to_PurchaseOrderItem": items,
        }
        return await self._post(
            "/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder", body
        )

    # ── Goods Receipt ──────────────────────────────────────────────────

    async def post_goods_receipt(self, **params) -> dict[str, Any]:
        """Post a goods receipt (material document) against a purchase order.

        Required params:
            goods_movement_code: Movement code (e.g. "01" for GR)
            posting_date: Posting date (YYYY-MM-DD)
            items: list of material document items, each with:
                - Material, Plant, StorageLocation, GoodsMovementType,
                  QuantityInEntryUnit, EntryUnit, PurchaseOrder, PurchaseOrderItem
        """
        goods_movement_code = params.get("goods_movement_code", "01")
        posting_date = params.get("posting_date", "")
        items = params.get("items", [])
        if not posting_date:
            return {"error": "posting_date is required"}
        if not items:
            return {"error": "items (material document items) are required"}

        body = {
            "GoodsMovementCode": goods_movement_code,
            "PostingDate": posting_date,
            "to_MaterialDocumentItem": items,
        }
        return await self._post(
            "/API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentHeader", body
        )

    # ── Payment Run ────────────────────────────────────────────────────

    async def run_payment_run(self, **params) -> dict[str, Any]:
        """Trigger an automatic payment run.

        Required params:
            company_code: SAP company code
            payment_method: Payment method key (e.g. "T" for bank transfer)
            run_date: Payment run date (YYYY-MM-DD)
        Optional params:
            vendor_from / vendor_to: Vendor number range
        """
        company_code = params.get("company_code", "")
        payment_method = params.get("payment_method", "")
        run_date = params.get("run_date", "")
        if not company_code or not payment_method or not run_date:
            return {"error": "company_code, payment_method, and run_date are required"}

        body: dict[str, Any] = {
            "CompanyCode": company_code,
            "PaymentMethod": payment_method,
            "RunDate": run_date,
        }
        if params.get("vendor_from"):
            body["VendorFrom"] = params["vendor_from"]
        if params.get("vendor_to"):
            body["VendorTo"] = params["vendor_to"]

        return await self._post("/API_PAYMENTRUN/A_PaymentRun", body)

    # ── Vendor Master ──────────────────────────────────────────────────

    async def get_vendor_master(self, **params) -> dict[str, Any]:
        """Retrieve vendor (supplier) business partners.

        Optional params:
            supplier: Specific supplier number
            search_term: Free-text search on BusinessPartnerName
            top: Max records (default 50)
        """
        odata_filter = "BusinessPartnerCategory eq '2'"
        if params.get("supplier"):
            odata_filter += f" and BusinessPartner eq '{params['supplier']}'"
        if params.get("search_term"):
            odata_filter += (
                f" and substringof('{params['search_term']}',BusinessPartnerName)"
            )

        query_params: dict[str, Any] = {"$filter": odata_filter}
        top = params.get("top", 50)
        query_params["$top"] = str(top)

        return await self._odata_get(
            "/API_BUSINESS_PARTNER/A_BusinessPartner",
            params=query_params,
        )

    # ── Cost Center ────────────────────────────────────────────────────

    async def get_cost_center(self, **params) -> dict[str, Any]:
        """Retrieve cost centers for a controlling area.

        Required params:
            controlling_area: Controlling area key (e.g. "0001")
        Optional params:
            cost_center: Specific cost center to filter
            top: Max records (default 100)
        """
        controlling_area = params.get("controlling_area", "")
        if not controlling_area:
            return {"error": "controlling_area is required"}

        odata_filter = f"ControllingArea eq '{controlling_area}'"
        if params.get("cost_center"):
            odata_filter += f" and CostCenter eq '{params['cost_center']}'"

        query_params: dict[str, Any] = {"$filter": odata_filter}
        top = params.get("top", 100)
        query_params["$top"] = str(top)

        return await self._odata_get(
            "/API_COSTCENTER_SRV/A_CostCenter",
            params=query_params,
        )
