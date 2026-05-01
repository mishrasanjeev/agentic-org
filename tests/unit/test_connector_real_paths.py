"""Verify all connectors use real API endpoints (not stubs).

Tests the top 20 most important connectors call correct API paths.
Uses unittest.mock to patch httpx and verify the path + HTTP method for each tool.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers — build a connector with a mock httpx client
# ---------------------------------------------------------------------------

def _mock_client_response(json_data=None):
    """Return a mock httpx.Response with .json(), .raise_for_status()."""
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    resp.status_code = 200
    return resp


def _async_response(json_data=None):
    """Return an AsyncMock that resolves to a mock response."""
    return AsyncMock(return_value=_mock_client_response(json_data))


# ═══════════════════════════════════════════════════════════════════════════
# Stripe
# ═══════════════════════════════════════════════════════════════════════════


class TestStripeRealPaths:
    """Verify Stripe connector hits correct Stripe API v1 paths."""

    def _make_connector(self):
        from connectors.finance.stripe import StripeConnector
        c = StripeConnector({"api_key": "sk_test_fake"})
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_create_payment_intent_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "pi_123", "status": "requires_payment_method"})
        await c.create_payment_intent(amount=5000, currency="inr")
        c._client.post.assert_called_once()
        args = c._client.post.call_args
        assert args[0][0] == "/v1/payment_intents"

    @pytest.mark.asyncio
    async def test_create_payment_intent_uses_form_encoding(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "pi_123"})
        await c.create_payment_intent(amount=5000, currency="inr")
        call_kwargs = c._client.post.call_args
        # _post_form uses data= (form-encoded), not json=
        assert "data" in call_kwargs.kwargs or (len(call_kwargs.args) > 1)

    @pytest.mark.asyncio
    async def test_get_balance_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"available": [], "pending": []})
        await c.get_balance()
        c._client.get.assert_called_once()
        args = c._client.get.call_args
        assert args[0][0] == "/v1/balance"

    @pytest.mark.asyncio
    async def test_list_charges_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"data": [], "has_more": False})
        await c.list_charges()
        args = c._client.get.call_args
        assert args[0][0] == "/v1/charges"

    @pytest.mark.asyncio
    async def test_create_payout_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "po_123"})
        await c.create_payout(amount=10000)
        args = c._client.post.call_args
        assert args[0][0] == "/v1/payouts"

    @pytest.mark.asyncio
    async def test_list_invoices_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"data": [], "has_more": False})
        await c.list_invoices()
        args = c._client.get.call_args
        assert args[0][0] == "/v1/invoices"

    @pytest.mark.asyncio
    async def test_create_customer_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "cus_123"})
        await c.create_customer(email="test@example.com")
        args = c._client.post.call_args
        assert args[0][0] == "/v1/customers"

    @pytest.mark.asyncio
    async def test_create_refund_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "re_123"})
        await c.create_refund(payment_intent="pi_123")
        args = c._client.post.call_args
        assert args[0][0] == "/v1/refunds"


# ═══════════════════════════════════════════════════════════════════════════
# Zoho Books
# ═══════════════════════════════════════════════════════════════════════════


class TestZohoBooksRealPaths:
    """Verify Zoho Books connector hits correct Zoho API v3 paths."""

    def _make_connector(self):
        from connectors.finance.zoho_books import ZohoBooksConnector
        c = ZohoBooksConnector({"access_token": "fake", "organization_id": "org123"})
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_create_invoice_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"invoice": {"invoice_id": "INV-1"}})
        await c.create_invoice(customer_id="cust_1", line_items=[{"item_id": "1"}])
        args = c._client.post.call_args
        assert args[0][0] == "/invoices"
        # Zoho requires organization_id as query param on POST
        assert args[1]["params"]["organization_id"] == "org123"

    @pytest.mark.asyncio
    async def test_list_invoices_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"invoices": []})
        await c.list_invoices()
        args = c._client.get.call_args
        assert args[0][0] == "/invoices"

    @pytest.mark.asyncio
    async def test_record_expense_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"expense": {}})
        await c.record_expense(account_id="acc1", amount=5000, date="2026-03-01")
        args = c._client.post.call_args
        assert args[0][0] == "/expenses"

    @pytest.mark.asyncio
    async def test_get_balance_sheet_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"balance_sheet": {}})
        await c.get_balance_sheet()
        args = c._client.get.call_args
        assert args[0][0] == "/reports/balancesheet"

    @pytest.mark.asyncio
    async def test_get_profit_loss_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"profit_and_loss": {"revenue": 1000}})
        await c.get_profit_loss(from_date="2026-04-01", to_date="2026-04-30")
        args = c._client.get.call_args
        assert args[0][0] == "/reports/profitandloss"

    @pytest.mark.asyncio
    async def test_list_chartofaccounts_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"chartofaccounts": [{"account_id": "a1"}]})
        out = await c.list_chartofaccounts(filter_by="AccountType.asset")
        args = c._client.get.call_args
        assert args[0][0] == "/chartofaccounts"
        assert isinstance(out["chartofaccounts"], list)

    @pytest.mark.asyncio
    async def test_reconcile_transaction_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"bank_transaction": {"transaction_id": "T1"}})
        await c.reconcile_transaction(
            account_id="a1", amount=1000, date="2026-05-01", reference_number="R1"
        )
        args = c._client.post.call_args
        assert args[0][0] == "/banktransactions/uncategorized"

    @pytest.mark.asyncio
    async def test_get_organization_path(self):
        c = self._make_connector()
        c._client.get = _async_response(
            {"organizations": [{"organization_id": "1", "name": "Acme"}]}
        )
        out = await c.get_organization()
        args = c._client.get.call_args
        assert args[0][0] == "/organizations"
        assert out["organizations"][0]["name"] == "Acme"

    @pytest.mark.asyncio
    async def test_get_organization_empty(self):
        c = self._make_connector()
        c._client.get = _async_response({"organizations": []})
        out = await c.get_organization()
        assert out == {"organizations": []}

    @pytest.mark.asyncio
    async def test_health_check_returns_org_count(self):
        c = self._make_connector()
        c._client.get = _async_response(
            {"organizations": [{"organization_id": "1"}, {"organization_id": "2"}]}
        )
        out = await c.health_check()
        assert out["status"] == "healthy"
        assert out["organizations"] == 2

    @pytest.mark.asyncio
    async def test_create_invoice_validates_required(self):
        c = self._make_connector()
        a = await c.create_invoice(line_items=[{"item_id": "i"}])
        b = await c.create_invoice(customer_id="c")
        assert "customer_id" in a["error"]
        assert "line_items" in b["error"]

    @pytest.mark.asyncio
    async def test_record_expense_validates_required(self):
        c = self._make_connector()
        a = await c.record_expense(amount=100, date="2026-05-01")
        b = await c.record_expense(account_id="a", date="2026-05-01")
        d = await c.record_expense(account_id="a", amount=100)
        assert "account_id" in a["error"]
        assert "amount" in b["error"]
        assert "date" in d["error"]

    @pytest.mark.asyncio
    async def test_reconcile_transaction_validates_required(self):
        c = self._make_connector()
        out = await c.reconcile_transaction(amount=100, date="2026-05-01")
        assert "account_id" in out["error"]


# ═══════════════════════════════════════════════════════════════════════════
# QuickBooks Online — every tool method against a mocked AsyncClient
# ═══════════════════════════════════════════════════════════════════════════


class TestQuickbooksRealPaths:
    """Verify QuickBooks Online connector hits correct QBO REST v3 paths."""

    def _make_connector(self):
        from connectors.finance.quickbooks import QuickbooksConnector
        c = QuickbooksConnector(
            {"access_token": "fake", "realm_id": "9341452961234567"}
        )
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_create_invoice_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"Invoice": {"Id": "1", "TotalAmt": 100}})
        out = await c.create_invoice(
            Line=[{"Amount": 100, "DetailType": "SalesItemLineDetail"}],
            CustomerRef={"value": "cust1"},
            TxnDate="2026-05-01",
        )
        args = c._client.post.call_args
        assert args[0][0] == "/company/9341452961234567/invoice"
        assert out.get("Id") == "1"

    @pytest.mark.asyncio
    async def test_create_invoice_normalises_string_customer_ref(self):
        c = self._make_connector()
        c._client.post = _async_response({"Invoice": {"Id": "2"}})
        out = await c.create_invoice(
            Line=[{"Amount": 50}], CustomerRef="cust-string"
        )
        assert out.get("Id") == "2"

    @pytest.mark.asyncio
    async def test_create_invoice_validates_required(self):
        c = self._make_connector()
        a = await c.create_invoice(CustomerRef={"value": "c"})
        b = await c.create_invoice(Line=[{}])
        assert "Line" in a["error"]
        assert "CustomerRef" in b["error"]

    @pytest.mark.asyncio
    async def test_record_payment_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"Payment": {"Id": "P1", "TotalAmt": 100}})
        out = await c.record_payment(
            TotalAmt=100,
            CustomerRef={"value": "c"},
            PaymentMethodRef={"value": "1"},
            DepositToAccountRef={"value": "33"},
        )
        args = c._client.post.call_args
        assert args[0][0] == "/company/9341452961234567/payment"
        assert out.get("Id") == "P1"

    @pytest.mark.asyncio
    async def test_record_payment_validates_required(self):
        c = self._make_connector()
        a = await c.record_payment(CustomerRef={"value": "c"})
        b = await c.record_payment(TotalAmt=50)
        assert "TotalAmt" in a["error"]
        assert "CustomerRef" in b["error"]

    @pytest.mark.asyncio
    async def test_record_payment_normalises_string_customer_ref(self):
        c = self._make_connector()
        c._client.post = _async_response({"Payment": {"Id": "P2"}})
        out = await c.record_payment(TotalAmt=200, CustomerRef="cust-string")
        assert out.get("Id") == "P2"

    @pytest.mark.asyncio
    async def test_get_profit_loss_path(self):
        c = self._make_connector()
        c._client.get = _async_response(
            {"Header": {"ReportName": "ProfitAndLoss"}, "Rows": {}}
        )
        out = await c.get_profit_loss(start_date="2026-04-01", end_date="2026-04-30")
        args = c._client.get.call_args
        assert args[0][0] == "/company/9341452961234567/reports/ProfitAndLoss"
        assert out["Header"]["ReportName"] == "ProfitAndLoss"

    @pytest.mark.asyncio
    async def test_get_balance_sheet_path(self):
        c = self._make_connector()
        c._client.get = _async_response(
            {"Header": {"ReportName": "BalanceSheet"}, "Rows": {}}
        )
        out = await c.get_balance_sheet(date="2026-04-30")
        args = c._client.get.call_args
        assert args[0][0] == "/company/9341452961234567/reports/BalanceSheet"
        assert out["Header"]["ReportName"] == "BalanceSheet"

    @pytest.mark.asyncio
    async def test_query_path_unwraps_query_response(self):
        c = self._make_connector()
        c._client.get = _async_response(
            {
                "QueryResponse": {"Invoice": [{"Id": "1"}, {"Id": "2"}]},
                "time": "2026-05-01",
            }
        )
        out = await c.query(query="SELECT * FROM Invoice")
        args = c._client.get.call_args
        assert args[0][0] == "/company/9341452961234567/query"
        assert isinstance(out, list)
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_query_validates_required(self):
        c = self._make_connector()
        out = await c.query()
        assert "query is required" in out["error"]

    @pytest.mark.asyncio
    async def test_get_company_info_path(self):
        c = self._make_connector()
        c._client.get = _async_response(
            {"CompanyInfo": {"CompanyName": "Acme Corp"}}
        )
        out = await c.get_company_info()
        args = c._client.get.call_args
        assert (
            args[0][0]
            == "/company/9341452961234567/companyinfo/9341452961234567"
        )
        assert out.get("CompanyName") == "Acme Corp"

    @pytest.mark.asyncio
    async def test_health_check_returns_company_name(self):
        c = self._make_connector()
        c._client.get = _async_response(
            {"CompanyInfo": {"CompanyName": "Acme Corp"}}
        )
        out = await c.health_check()
        assert out["status"] == "healthy"
        assert out["company_name"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_on_error(self):
        c = self._make_connector()
        c._client.get = AsyncMock(side_effect=RuntimeError("boom"))
        out = await c.health_check()
        assert out["status"] == "unhealthy"

    def test_company_path_embeds_realm_id(self):
        c = self._make_connector()
        assert c._company_path("invoice") == "/company/9341452961234567/invoice"

    def test_unwrap_handles_query_response_envelope(self):
        c = self._make_connector()
        out = c._unwrap({"QueryResponse": {"Invoice": [{"Id": "1"}]}})
        assert out == [{"Id": "1"}]

    def test_unwrap_handles_direct_entity_envelope(self):
        c = self._make_connector()
        out = c._unwrap({"Invoice": {"Id": "1"}}, "Invoice")
        assert out == {"Id": "1"}

    def test_unwrap_skips_time_metadata_key(self):
        c = self._make_connector()
        out = c._unwrap({"time": "2026-05-01", "Invoice": {"Id": "1"}})
        assert out == {"Id": "1"}

    def test_unwrap_passes_through_non_dict(self):
        c = self._make_connector()
        assert c._unwrap("not a dict") == "not a dict"


# ═══════════════════════════════════════════════════════════════════════════
# NetSuite — every tool method against a mocked AsyncClient
# ═══════════════════════════════════════════════════════════════════════════


class TestNetsuiteRealPaths:
    """Verify NetSuite SuiteTalk REST connector hits correct record paths."""

    def _make_connector(self):
        from connectors.finance.netsuite import NetsuiteConnector
        c = NetsuiteConnector(
            {"account_id": "TSTDRV1234567", "access_token": "fake-token"}
        )
        c._client = MagicMock()
        return c

    def test_base_url_built_from_account_id(self):
        c = self._make_connector()
        assert c.base_url.startswith("https://tstdrv1234567.suitetalk.api.netsuite.com")

    def test_base_url_lowercases_and_replaces_dots(self):
        from connectors.finance.netsuite import NetsuiteConnector
        c = NetsuiteConnector({"account_id": "TSTDRV.1234.567", "access_token": "x"})
        assert "tstdrv_1234_567.suitetalk.api.netsuite.com" in c.base_url

    @pytest.mark.asyncio
    async def test_create_invoice_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "inv1"})
        await c.create_invoice(
            entity="cust1",
            item=[{"item": {"id": "42"}, "quantity": 2, "rate": 150}],
            tranDate="2026-05-01",
            memo="May invoice",
            department="DEPT-1",
        )
        args = c._client.post.call_args
        assert args[0][0] == "/invoice"

    @pytest.mark.asyncio
    async def test_create_invoice_validates_required(self):
        c = self._make_connector()
        a = await c.create_invoice(item=[{}])
        b = await c.create_invoice(entity="cust1")
        assert "entity" in a["error"]
        assert "item" in b["error"]

    @pytest.mark.asyncio
    async def test_get_invoice_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"id": "inv1"})
        await c.get_invoice(id="inv1", expandSubResources=True)
        args = c._client.get.call_args
        assert args[0][0] == "/invoice/inv1"

    @pytest.mark.asyncio
    async def test_get_invoice_validates_required(self):
        c = self._make_connector()
        out = await c.get_invoice()
        assert "id" in out["error"]

    @pytest.mark.asyncio
    async def test_create_journal_entry_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "je1"})
        await c.create_journal_entry(
            subsidiary="1",
            line=[
                {"account": {"id": "10"}, "debit": 1000, "memo": "Rev"},
                {"account": {"id": "20"}, "credit": 1000, "memo": "Cash"},
            ],
            tranDate="2026-05-01",
            memo="Adj entry",
        )
        args = c._client.post.call_args
        assert args[0][0] == "/journalEntry"

    @pytest.mark.asyncio
    async def test_create_journal_entry_validates_required(self):
        c = self._make_connector()
        a = await c.create_journal_entry(line=[{}])
        b = await c.create_journal_entry(subsidiary="1")
        assert "subsidiary" in a["error"]
        assert "line" in b["error"]

    @pytest.mark.asyncio
    async def test_get_account_balance_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"id": "10", "balance": 5000})
        await c.get_account_balance(id="10")
        args = c._client.get.call_args
        assert args[0][0] == "/account/10"

    @pytest.mark.asyncio
    async def test_get_account_balance_validates_required(self):
        c = self._make_connector()
        out = await c.get_account_balance()
        assert "id" in out["error"]

    @pytest.mark.asyncio
    async def test_create_vendor_bill_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "vb1"})
        await c.create_vendor_bill(
            entity="vendor1",
            item=[{"item": {"id": "55"}, "quantity": 1, "rate": 500}],
            tranDate="2026-05-01",
            memo="Office supplies",
        )
        args = c._client.post.call_args
        assert args[0][0] == "/vendorBill"

    @pytest.mark.asyncio
    async def test_create_vendor_bill_validates_required(self):
        c = self._make_connector()
        a = await c.create_vendor_bill(item=[{}])
        b = await c.create_vendor_bill(entity="v")
        assert "entity" in a["error"]
        assert "item" in b["error"]

    @pytest.mark.asyncio
    async def test_create_purchase_order_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "po1"})
        await c.create_purchase_order(
            entity="vendor1",
            item=[{"item": {"id": "77"}, "quantity": 10, "rate": 25}],
            tranDate="2026-05-01",
            memo="Bulk order",
            shipTo="100",
        )
        args = c._client.post.call_args
        assert args[0][0] == "/purchaseOrder"

    @pytest.mark.asyncio
    async def test_create_purchase_order_validates_required(self):
        c = self._make_connector()
        a = await c.create_purchase_order(item=[{}])
        b = await c.create_purchase_order(entity="v")
        assert "entity" in a["error"]
        assert "item" in b["error"]

    @pytest.mark.asyncio
    async def test_get_trial_balance_path(self):
        c = self._make_connector()
        c._client.get = _async_response(
            {
                "items": [
                    {
                        "id": "10",
                        "acctName": "Cash",
                        "acctNumber": "1000",
                        "acctType": {"refName": "Bank"},
                        "balance": 50000,
                    }
                ],
                "totalResults": 1,
            }
        )
        out = await c.get_trial_balance(period="P1", subsidiary="1")
        args = c._client.get.call_args
        assert args[0][0] == "/account"
        assert out["accounts"][0]["acctName"] == "Cash"
        assert out["accounts"][0]["acctType"] == "Bank"

    @pytest.mark.asyncio
    async def test_search_records_path(self):
        c = self._make_connector()
        c._client.get = _async_response(
            {"items": [{"id": "1"}], "totalResults": 1, "hasMore": False}
        )
        out = await c.search_records(
            record_type="invoice", q="status='open'", limit=50, offset=0
        )
        args = c._client.get.call_args
        assert args[0][0] == "/invoice"
        assert out["records"][0]["id"] == "1"

    @pytest.mark.asyncio
    async def test_search_records_validates_required(self):
        c = self._make_connector()
        out = await c.search_records()
        assert "record_type" in out["error"]

    @pytest.mark.asyncio
    async def test_health_check_returns_total_results(self):
        c = self._make_connector()
        c._client.get = _async_response({"totalResults": 7})
        out = await c.health_check()
        assert out["status"] == "healthy"
        assert out["total_results"] == 7

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_on_error(self):
        c = self._make_connector()
        c._client.get = AsyncMock(side_effect=RuntimeError("boom"))
        out = await c.health_check()
        assert out["status"] == "unhealthy"


# ═══════════════════════════════════════════════════════════════════════════
# Oracle Fusion
# ═══════════════════════════════════════════════════════════════════════════


class TestOracleFusionRealPaths:
    """Verify Oracle Fusion connector hits correct Oracle REST paths."""

    def _make_connector(self):
        from connectors.finance.oracle_fusion import OracleFusionConnector
        c = OracleFusionConnector({"username": "user", "password": "pass"})
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_post_journal_entry_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"JournalHeaderId": 1234})
        await c.post_journal_entry(
            LedgerName="US Primary Ledger",
            AccountingDate="2026-03-01",
            JournalLines=[{"AccountCombination": "101-00-1000", "DebitAmount": 5000, "CreditAmount": 0}],
        )
        args = c._client.post.call_args
        assert args[0][0] == "/generalLedgerJournals"

    @pytest.mark.asyncio
    async def test_get_gl_balance_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"items": [], "count": 0})
        await c.get_gl_balance()
        args = c._client.get.call_args
        assert args[0][0] == "/generalLedgerBalances"

    @pytest.mark.asyncio
    async def test_create_ap_invoice_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"InvoiceId": "AP-001"})
        await c.create_ap_invoice(
            InvoiceNumber="INV-1", InvoiceDate="2026-03-01",
            Supplier="Acme Corp", InvoiceAmount=50000,
        )
        args = c._client.post.call_args
        assert args[0][0] == "/payablesInvoices"

    @pytest.mark.asyncio
    async def test_create_po_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"OrderNumber": "PO-001"})
        await c.create_po(
            OrderNumber="PO-001", Supplier="Acme",
            Lines=[{"LineNumber": 1, "ItemDescription": "Laptops", "Quantity": 10, "UnitPrice": 800}],
        )
        args = c._client.post.call_args
        assert args[0][0] == "/purchaseOrders"


# ═══════════════════════════════════════════════════════════════════════════
# Salesforce
# ═══════════════════════════════════════════════════════════════════════════


class TestSalesforceRealPaths:
    """Verify Salesforce connector hits correct SFDC REST API v60.0 paths."""

    def _make_connector(self):
        from connectors.marketing.salesforce import SalesforceConnector
        c = SalesforceConnector({"client_id": "x", "client_secret": "y"})
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_query_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"records": [], "totalSize": 0})
        await c.query(q="SELECT Id FROM Account LIMIT 1")
        args = c._client.get.call_args
        assert args[0][0] == "/query"

    @pytest.mark.asyncio
    async def test_query_passes_soql(self):
        c = self._make_connector()
        c._client.get = _async_response({"records": []})
        soql = "SELECT Id, Name FROM Account"
        await c.query(q=soql)
        args = c._client.get.call_args
        # SOQL is passed via params (positional or keyword)
        params_arg = args[1].get("params") or (args[0][1] if len(args[0]) > 1 else {})
        assert params_arg.get("q") == soql

    @pytest.mark.asyncio
    async def test_create_lead_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "00Q123", "success": True})
        await c.create_lead(LastName="Test", Company="Acme")
        args = c._client.post.call_args
        assert args[0][0] == "/sobjects/Lead"

    @pytest.mark.asyncio
    async def test_create_task_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "00T123"})
        await c.create_task(Subject="Follow up")
        args = c._client.post.call_args
        assert args[0][0] == "/sobjects/Task"


# ═══════════════════════════════════════════════════════════════════════════
# Slack
# ═══════════════════════════════════════════════════════════════════════════


class TestSlackRealPaths:
    """Verify Slack connector hits correct Slack Web API paths."""

    def _make_connector(self):
        from connectors.comms.slack import SlackConnector
        c = SlackConnector({"bot_token": "xoxb-fake"})
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_send_message_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"ok": True, "channel": "C123", "ts": "123.456"})
        await c.send_message(channel="C123", text="Hello")
        args = c._client.post.call_args
        assert args[0][0] == "/chat.postMessage"

    @pytest.mark.asyncio
    async def test_create_channel_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"ok": True, "channel": {"id": "C999", "name": "test"}})
        await c.create_channel(name="test-channel")
        args = c._client.post.call_args
        assert args[0][0] == "/conversations.create"

    @pytest.mark.asyncio
    async def test_list_channels_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"ok": True, "channels": [], "response_metadata": {}})
        await c.list_channels()
        args = c._client.get.call_args
        assert args[0][0] == "/conversations.list"

    @pytest.mark.asyncio
    async def test_post_alert_uses_chat_post_message(self):
        c = self._make_connector()
        c._client.post = _async_response({"ok": True, "channel": "C123", "ts": "1.2"})
        await c.post_alert(channel="C123", title="Test Alert", message="Something happened")
        args = c._client.post.call_args
        assert args[0][0] == "/chat.postMessage"


# ═══════════════════════════════════════════════════════════════════════════
# Google Ads
# ═══════════════════════════════════════════════════════════════════════════


class TestGoogleAdsRealPaths:
    """Verify Google Ads connector hits correct Google Ads API v17 paths."""

    def _make_connector(self):
        from connectors.marketing.google_ads import GoogleAdsConnector
        c = GoogleAdsConnector({
            "customer_id": "123-456-7890",
            "developer_token": "devtoken",
            "client_id": "x",
            "client_secret": "y",
        })
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_search_campaigns_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"results": []})
        await c.search_campaigns()
        args = c._client.post.call_args
        path = args[0][0]
        assert "/customers/1234567890/googleAds:searchStream" == path

    @pytest.mark.asyncio
    async def test_get_campaign_performance_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"results": []})
        await c.get_campaign_performance(start_date="2026-03-01", end_date="2026-03-31")
        args = c._client.post.call_args
        path = args[0][0]
        assert "/customers/1234567890/googleAds:searchStream" == path

    @pytest.mark.asyncio
    async def test_mutate_campaign_budget_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"results": []})
        await c.mutate_campaign_budget(campaign_budget_id="budget123", amount_micros=5000000)
        args = c._client.post.call_args
        path = args[0][0]
        assert "/customers/1234567890/campaignBudgets:mutate" == path


# ═══════════════════════════════════════════════════════════════════════════
# Okta
# ═══════════════════════════════════════════════════════════════════════════


class TestOktaRealPaths:
    """Verify Okta connector hits correct Okta Management API v1 paths."""

    def _make_connector(self):
        from connectors.hr.okta import OktaConnector
        c = OktaConnector({"api_token": "fake-token"})
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_provision_user_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "user123", "status": "ACTIVE"})
        await c.provision_user(firstName="John", lastName="Doe", email="john@example.com")
        args = c._client.post.call_args
        path = args[0][0]
        assert path.startswith("/users")
        assert "activate=true" in path

    @pytest.mark.asyncio
    async def test_deactivate_user_path(self):
        c = self._make_connector()
        c._client.post = _async_response({})
        await c.deactivate_user(user_id="user123")
        args = c._client.post.call_args
        assert args[0][0] == "/users/user123/lifecycle/deactivate"

    @pytest.mark.asyncio
    async def test_assign_group_uses_put(self):
        c = self._make_connector()
        c._client.put = _async_response({})
        await c.assign_group(group_id="grp1", user_id="usr1")
        c._client.put.assert_called_once()
        args = c._client.put.call_args
        assert args[0][0] == "/groups/grp1/users/usr1"

    @pytest.mark.asyncio
    async def test_reset_mfa_path(self):
        c = self._make_connector()
        c._client.post = _async_response({})
        await c.reset_mfa(user_id="user123")
        args = c._client.post.call_args
        assert args[0][0] == "/users/user123/lifecycle/reset_factors"


# ═══════════════════════════════════════════════════════════════════════════
# GA4
# ═══════════════════════════════════════════════════════════════════════════


class TestGA4RealPaths:
    """Verify GA4 connector hits correct Google Analytics Data API v1beta paths."""

    def _make_connector(self):
        from connectors.marketing.ga4 import GA4Connector
        c = GA4Connector({"property_id": "987654321", "client_id": "x", "client_secret": "y"})
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_run_report_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"rows": []})
        await c.run_report()
        args = c._client.post.call_args
        assert args[0][0] == "/properties/987654321:runReport"

    @pytest.mark.asyncio
    async def test_run_realtime_report_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"rows": []})
        await c.run_realtime_report()
        args = c._client.post.call_args
        assert args[0][0] == "/properties/987654321:runRealtimeReport"

    @pytest.mark.asyncio
    async def test_get_metadata_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"dimensions": [], "metrics": []})
        await c.get_metadata()
        args = c._client.get.call_args
        assert args[0][0] == "/properties/987654321/metadata"

    @pytest.mark.asyncio
    async def test_get_conversions_uses_run_report(self):
        c = self._make_connector()
        c._client.post = _async_response({"rows": []})
        await c.get_conversions()
        args = c._client.post.call_args
        assert args[0][0] == "/properties/987654321:runReport"


# ═══════════════════════════════════════════════════════════════════════════
# HubSpot
# ═══════════════════════════════════════════════════════════════════════════


class TestHubSpotRealPaths:
    """Verify HubSpot connector hits correct HubSpot CRM v3 paths (already real, verify it stays)."""

    def _make_connector(self):
        from connectors.marketing.hubspot import HubspotConnector
        c = HubspotConnector({"access_token": "fake-token"})
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_list_contacts_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"results": [], "total": 0})
        await c.list_contacts()
        args = c._client.get.call_args
        assert args[0][0] == "/crm/v3/objects/contacts"

    @pytest.mark.asyncio
    async def test_create_contact_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "101"})
        await c.create_contact(email="test@example.com", firstname="John")
        args = c._client.post.call_args
        assert args[0][0] == "/crm/v3/objects/contacts"

    @pytest.mark.asyncio
    async def test_list_deals_path(self):
        c = self._make_connector()
        c._client.get = _async_response({"results": [], "total": 0})
        await c.list_deals()
        args = c._client.get.call_args
        assert args[0][0] == "/crm/v3/objects/deals"

    @pytest.mark.asyncio
    async def test_create_company_path(self):
        c = self._make_connector()
        c._client.post = _async_response({"id": "201"})
        await c.create_company(name="Test Corp")
        args = c._client.post.call_args
        assert args[0][0] == "/crm/v3/objects/companies"


# ═══════════════════════════════════════════════════════════════════════════
# PineLabs Plural
# ═══════════════════════════════════════════════════════════════════════════


class TestPineLabsRealPaths:
    """Verify PineLabs Plural connector hits correct Plural API v1 paths."""

    def _make_connector(self):
        from connectors.finance.pinelabs_plural import PinelabsPluralConnector
        c = PinelabsPluralConnector({
            "client_id": "test_client",
            "client_secret": "test_secret",
            "merchant_id": "M001",
        })
        # _authenticate() sets these — set manually since we skip auth
        c._client_id = "test_client"
        c._client_secret = "test_secret"
        c._merchant_id = "M001"
        c._access_token = "test_bearer_token"
        c._token_expires = 9999999999.0
        c._auth_headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer test_bearer_token",
        }
        c.base_url = "https://pluraluat.v2.pinepg.in/api"
        c._client = MagicMock()
        return c

    @pytest.mark.asyncio
    async def test_create_order_path(self):
        c = self._make_connector()
        c._client.post = _async_response({
            "order_id": "v1-ord-1", "redirect_url": "https://checkout.test/pay",
            "response_code": 200,
        })
        await c.create_order(merchant_order_reference="ref1", amount=100000)
        args = c._client.post.call_args
        path = args[0][0]
        assert "/checkout/v1/orders" in path

    @pytest.mark.asyncio
    async def test_check_order_status_path(self):
        c = self._make_connector()
        c._client.get = _async_response({
            "order_id": "v1-ord-1", "status": "PROCESSED", "payments": [],
        })
        await c.get_order_status(order_id="v1-ord-1")
        args = c._client.get.call_args
        path = args[0][0]
        assert "/pay/v1/orders/v1-ord-1" in path

    @pytest.mark.asyncio
    async def test_initiate_refund_path(self):
        c = self._make_connector()
        c._client.post = _async_response({
            "refund_id": "ref-1", "status": "PENDING", "refund_amount": {},
        })
        await c.initiate_refund(order_id="v1-ord-1", amount=50000)
        args = c._client.post.call_args
        path = args[0][0]
        assert "/pay/v1/orders/v1-ord-1/refunds" in path

    @pytest.mark.asyncio
    async def test_create_order_has_bearer_auth(self):
        c = self._make_connector()
        c._client.post = _async_response({
            "order_id": "v1-ord-2", "challenge_url": "https://checkout.test/pay",
            "status": "CREATED",
        })
        await c.create_order(merchant_order_reference="ref2", amount=200000)
        args = c._client.post.call_args
        headers = args[1].get("headers", {})
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert "Request-ID" in headers
        assert "Request-Timestamp" in headers
