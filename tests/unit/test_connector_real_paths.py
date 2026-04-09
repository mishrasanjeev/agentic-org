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
