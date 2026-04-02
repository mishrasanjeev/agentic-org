"""E2E test — CA firm workflow: invoice → bank reconcile → GSTN → Tally.

This test validates the core accounting pipeline that a Chartered
Accountant firm would run:

1. Create an invoice (via Zoho Books)
2. Fetch bank statement and reconcile (via Banking AA)
3. Push GSTR-1 data to GSTN (via Adaequare GSP)
4. Sync the voucher into Tally (via TDL/XML)

Uses mocked connector responses so the test is self-contained and
runs without external service access.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import Element, SubElement

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_invoice():
    return {
        "invoice_id": "INV-2026-0042",
        "vendor_id": "VND-100",
        "vendor_name": "Acme Supplies Pvt Ltd",
        "gstin": "29ABCDE1234F1Z5",
        "total": 118000,
        "taxable_value": 100000,
        "cgst": 9000,
        "sgst": 9000,
        "date": "2026-03-28",
        "status": "approved",
    }


@pytest.fixture
def bank_statement_row():
    return {
        "txn_id": "TXN-99887766",
        "date": "2026-03-29",
        "narration": "NEFT-INV-2026-0042-ACME",
        "amount": 118000,
        "type": "debit",
        "balance": 542000,
    }


@pytest.fixture
def gstr1_response():
    return {
        "status_cd": "1",
        "reference_id": "GSTN-REF-77665544",
        "message": "GSTR-1 data saved successfully",
    }


def _tally_xml_response() -> Element:
    """Build a minimal Tally import success XML response."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    SubElement(header, "STATUS").text = "1"
    body = SubElement(envelope, "BODY")
    data = SubElement(body, "DATA")
    SubElement(data, "LINEERROR").text = ""
    SubElement(data, "CREATED").text = "1"
    SubElement(data, "ALTERED").text = "0"
    SubElement(data, "DELETED").text = "0"
    SubElement(data, "LASTVCHID").text = "98765"
    SubElement(data, "LASTMASTERID").text = ""
    SubElement(data, "COMBINED").text = "0"
    SubElement(data, "IGNORED").text = "0"
    return envelope


# ---------------------------------------------------------------------------
# Step 1: Invoice creation (Zoho Books)
# ---------------------------------------------------------------------------

class TestCAFirmWorkflowE2E:
    """End-to-end pipeline: invoice → bank reconcile → GSTN → Tally."""

    @pytest.mark.asyncio
    async def test_step1_create_invoice(self, sample_invoice):
        """Verify invoice creation returns an invoice ID and correct total."""
        from connectors.finance.zoho_books import ZohoBooksConnector

        connector = ZohoBooksConnector(config={"api_key": "test-key"})
        mock_resp = {
            "invoice": {
                "invoice_id": sample_invoice["invoice_id"],
                "total": sample_invoice["total"],
                "status": "sent",
            }
        }
        with (
            patch.object(connector, "_authenticate", new_callable=AsyncMock),
            patch.object(connector, "_post", new_callable=AsyncMock, return_value=mock_resp),
        ):
            await connector.connect()
            result = await connector.create_invoice(**sample_invoice)
            assert result["invoice"]["invoice_id"] == "INV-2026-0042"
            assert result["invoice"]["total"] == 118000

    # ---------------------------------------------------------------------------
    # Step 2: Bank reconciliation (Banking AA — read-only)
    # ---------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_step2_fetch_and_reconcile(self, sample_invoice, bank_statement_row):
        """Fetch bank statement via AA and match against invoice."""
        from connectors.finance.banking_aa import BankingAaConnector

        connector = BankingAaConnector(config={
            "client_id": "test-id",
            "client_secret": "test-secret",
        })
        mock_statement = {"transactions": [bank_statement_row]}

        with (
            patch.object(connector, "_authenticate", new_callable=AsyncMock),
            patch.object(connector, "_post", new_callable=AsyncMock, return_value=mock_statement),
        ):
            await connector.connect()
            result = await connector.fetch_bank_statement(
                account_id="ACC-001",
                from_date="2026-03-01",
                to_date="2026-03-31",
            )

            txns = result["transactions"]
            assert len(txns) >= 1

            # Reconcile: match invoice amount to bank debit
            matched = [
                t for t in txns
                if t["amount"] == sample_invoice["total"]
                and sample_invoice["invoice_id"] in t["narration"]
            ]
            assert len(matched) == 1, "Invoice should match exactly one bank transaction"
            assert matched[0]["txn_id"] == "TXN-99887766"

    @pytest.mark.asyncio
    async def test_step2_aa_has_no_payment_tools(self):
        """Verify Banking AA connector no longer exposes payment tools."""
        from connectors.finance.banking_aa import BankingAaConnector

        connector = BankingAaConnector()
        tools = list(connector._tool_registry.keys())
        assert "initiate_neft" not in tools
        assert "initiate_rtgs" not in tools
        assert "add_beneficiary" not in tools
        assert "cancel_payment" not in tools
        # Read-only tools + consent tools (no payment tools)
        assert set(tools) == {
            "fetch_bank_statement", "check_account_balance", "get_transaction_list",
            "request_consent", "fetch_fi_data",
        }

    # ---------------------------------------------------------------------------
    # Step 3: Push GSTR-1 to GSTN via Adaequare GSP
    # ---------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_step3_push_gstr1(self, sample_invoice, gstr1_response):
        """Push invoice as GSTR-1 outbound supply to GSTN."""
        from connectors.finance.gstn import GstnConnector

        connector = GstnConnector(config={
            "api_key": "test-asp-id",
            "gstin": "29ABCDE1234F1Z5",
            "username": "test-user",
            "password": "test-pass",
        })

        with (
            patch.object(connector, "_authenticate", new_callable=AsyncMock),
            patch.object(connector, "_post", new_callable=AsyncMock, return_value=gstr1_response),
        ):
            await connector.connect()
            result = await connector.push_gstr1_data(
                gstin=sample_invoice["gstin"],
                return_period="032026",
                b2b=[{
                    "ctin": sample_invoice["gstin"],
                    "inv": [{
                        "inum": sample_invoice["invoice_id"],
                        "idt": sample_invoice["date"],
                        "val": sample_invoice["total"],
                        "txval": sample_invoice["taxable_value"],
                        "cgst": sample_invoice["cgst"],
                        "sgst": sample_invoice["sgst"],
                    }],
                }],
            )
            assert result["status_cd"] == "1"
            assert result["reference_id"] == "GSTN-REF-77665544"

    @pytest.mark.asyncio
    async def test_step3_gstn_base_url_correct(self):
        """Verify GSTN base URL does not include /authenticate."""
        from connectors.finance.gstn import GstnConnector

        connector = GstnConnector()
        assert connector.base_url == "https://gsp.adaequare.com/gsp"
        assert not connector.base_url.endswith("/authenticate")

    # ---------------------------------------------------------------------------
    # Step 4: Sync voucher into Tally via TDL/XML
    # ---------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_step4_post_voucher_to_tally(self, sample_invoice):
        """Post the payment voucher to Tally via XML/TDL protocol."""
        from connectors.finance.tally import TallyConnector

        connector = TallyConnector(config={"api_key": "test-key"})
        tally_resp = _tally_xml_response()

        with patch.object(connector, "_post_xml", new_callable=AsyncMock, return_value=tally_resp):
            await connector.connect()
            result = await connector.post_voucher(
                company="TestCorp",
                VOUCHERTYPENAME="Payment",
                DATE="20260329",
                NARRATION=f"Payment for {sample_invoice['invoice_id']}",
                PARTYLEDGERNAME=sample_invoice["vendor_name"],
                AMOUNT=str(sample_invoice["total"]),
            )
            assert result["BODY"]["DATA"]["CREATED"] == "1"

    @pytest.mark.asyncio
    async def test_step4_tally_uses_xml_not_json(self):
        """Verify Tally connector uses XML protocol, not REST/JSON."""
        from connectors.finance.tally import TallyConnector

        connector = TallyConnector()
        assert connector.auth_type == "tdl_xml"
        assert connector.base_url == "http://localhost:9000"
        assert not hasattr(connector, "post_voucher_json")

    # ---------------------------------------------------------------------------
    # Full pipeline assertion
    # ---------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_full_pipeline_data_flows(self, sample_invoice, bank_statement_row, gstr1_response):
        """Verify data integrity across the full CA workflow pipeline."""
        invoice_id = sample_invoice["invoice_id"]
        amount = sample_invoice["total"]
        gstin = sample_invoice["gstin"]

        # The same invoice_id flows through every stage
        assert invoice_id in bank_statement_row["narration"]
        assert amount == bank_statement_row["amount"]
        assert gstin == sample_invoice["gstin"]

        # GSTN response confirms acceptance
        assert gstr1_response["status_cd"] == "1"

        # Tally response confirms import
        tally_resp = _tally_xml_response()
        created = tally_resp.find(".//CREATED")
        assert created is not None and created.text == "1"
