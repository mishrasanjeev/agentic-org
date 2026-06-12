"""Ramesh 12 Jun 2026 CA feedback regressions.

These tests pin real behavior, not just UI labels:
- TRACES is a separate connector from Income Tax 26AS and reconciles TDS rows.
- GSTN e-way bill generation validates Part A and Part B before provider calls.
- Bulk client upload accepts spreadsheet-style rows and rejects bad identifiers.
- Professional Tax prepares state-portal-ready challan payloads without stale hardcoded slabs.
- Client Portal uses signed invite/access tokens and stores only token hashes.
- CA firm billing calculates client invoice totals separately from platform billing.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_traces_reconciliation_reports_missing_extra_and_amount_mismatch() -> None:
    import connectors  # noqa: F401
    from connectors.finance.traces import TracesConnector
    from connectors.registry import ConnectorRegistry

    assert ConnectorRegistry.get("traces") is TracesConnector
    connector = TracesConnector({})

    result = await connector.reconcile_tds_with_traces(
        expected_deductions=[
            {
                "id": "books-1",
                "deductee_pan": "AABCU9603R",
                "section": "194C",
                "transaction_date": "2026-04-15",
                "challan_serial": "10244",
                "bsr_code": "0510002",
                "tds_amount": "2500",
            },
            {
                "id": "books-2",
                "deductee_pan": "BBBBB1111B",
                "section": "194J",
                "tds_amount": "5000",
            },
            {
                "id": "books-3",
                "deductee_pan": "CCCCC2222C",
                "section": "194H",
                "tds_amount": "1000",
            },
        ],
        traces_statement=[
            {
                "certificate_number": "traces-1",
                "deductee_pan": "AABCU9603R",
                "section": "194C",
                "transaction_date": "2026-04-15",
                "challan_serial": "10244",
                "bsr_code": "0510002",
                "tds_amount": "2500",
            },
            {
                "certificate_number": "traces-2",
                "deductee_pan": "BBBBB1111B",
                "section": "194J",
                "tds_amount": "4500",
            },
            {
                "certificate_number": "traces-3",
                "deductee_pan": "DDDDD3333D",
                "section": "194C",
                "tds_amount": "700",
            },
        ],
        tolerance_rupees=1,
    )

    assert result["status"] == "needs_review"
    assert result["summary"]["matched"] == 1
    assert result["summary"]["amount_mismatches"] == 1
    assert result["summary"]["missing_in_traces"] == 1
    assert result["summary"]["extra_in_traces"] == 1

    validation = await connector.validate_traces_rows(rows=[{"deductee_pan": "", "section": "194C"}])
    assert validation["status"] == "invalid"
    assert validation["summary"]["failed"] == 1


@pytest.mark.asyncio
async def test_gstn_eway_bill_bulk_validates_part_a_and_part_b() -> None:
    from connectors.finance.gstn import GstnConnector

    valid_invoice = {
        "client_reference": "INV-001",
        "supply_type": "outward",
        "sub_supply_type": "supply",
        "document_type": "tax_invoice",
        "document_number": "INV-001",
        "document_date": "2026-06-12",
        "from_gstin": "29AABCU9603R1ZM",
        "from_pin_code": "560001",
        "from_state_code": "29",
        "to_gstin": "27AABCU9603R1ZV",
        "to_pin_code": "400001",
        "to_state_code": "27",
        "product_name": "Machine parts",
        "hsn_code": "8483",
        "quantity": "10",
        "unit": "NOS",
        "taxable_amount": "100000",
        "total_invoice_value": "118000",
        "transport_mode": "road",
        "distance_km": "980",
        "vehicle_number": "KA01AB1234",
    }
    invalid_invoice = {**valid_invoice, "document_number": "INV-002"}
    invalid_invoice.pop("vehicle_number")

    connector = GstnConnector({})
    result = await connector.bulk_generate_eway_bills(
        invoices=[valid_invoice, invalid_invoice],
        submit=False,
    )

    assert result["summary"]["generated"] == 1
    assert result["summary"]["failed"] == 1
    assert result["generated"][0]["result"]["payload"]["part_a"]["from_pin_code"] == 560001
    assert "vehicle_number_or_transporter_id" in result["failed"][0]["error"]


def test_bulk_company_upload_parser_and_validator_accepts_sheet_rows() -> None:
    from api.v1.companies import _normalise_company_upload_row, _parse_company_upload

    csv_body = (
        "Company Name,GSTIN,PAN,State,Industry\n"
        "Acme Manufacturing Pvt Ltd,29AABCU9603R1ZM,AABCU9603R,Karnataka,Manufacturing\n"
        "Broken Client,INVALID,AABCU9603R,KA,Manufacturing\n"
    )
    rows = _parse_company_upload("clients.csv", csv_body.encode("utf-8"))

    first, first_errors = _normalise_company_upload_row(rows[0][1], rows[0][0])
    second, second_errors = _normalise_company_upload_row(rows[1][1], rows[1][0])

    assert first_errors == []
    assert first is not None
    assert first.state_code == "KA"
    assert first.gstin == "29AABCU9603R1ZM"
    assert second is None
    assert any("gstin format is invalid" in error for error in second_errors)


@pytest.mark.asyncio
async def test_professional_tax_connector_prepares_state_portal_payloads() -> None:
    import connectors  # noqa: F401
    from connectors.finance.professional_tax import ProfessionalTaxConnector
    from connectors.registry import ConnectorRegistry

    assert ConnectorRegistry.get("professional_tax") is ProfessionalTaxConnector
    connector = ProfessionalTaxConnector({})
    states = await connector.list_professional_tax_states()
    assert any(item["state_code"] == "KA" for item in states["items"])

    prepared = await connector.prepare_professional_tax_return(
        state_code="KA",
        filing_period="2026-06",
        registration_number="PT-KA-12345",
        employer_name="Acme Manufacturing",
        employees=[
            {"employee_id": "E001", "gross_salary": "60000", "pt_amount": "200"},
            {"employee_id": "E002", "gross_salary": "42000", "pt_amount": "200"},
        ],
    )

    assert prepared["status"] == "ready_for_filing"
    assert prepared["payload"]["employee_count"] == 2
    assert prepared["payload"]["pt_amount"] == "400.00"
    assert prepared["challan_draft"]["amount"] == "400.00"

    submitted = await connector.submit_professional_tax_return(
        state_code="KA",
        payload=prepared["payload"],
        dry_run=False,
    )
    assert submitted["status"] == "not_connected"
    assert "credentials" in submitted["reason"].lower()


def test_client_portal_invite_tokens_are_signed_and_hashable() -> None:
    import uuid
    from datetime import UTC, datetime, timedelta

    from api.v1.client_portal import (
        INVITE_TOKEN_PREFIX,
        _decode_signed_token,
        _hash_token,
        _new_invite_token,
    )

    tenant_id = uuid.uuid4()
    company_id = uuid.uuid4()
    invite_id = uuid.uuid4()
    token = _new_invite_token(
        tenant_id=tenant_id,
        company_id=company_id,
        invite_id=invite_id,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    payload = _decode_signed_token(token, INVITE_TOKEN_PREFIX)

    assert payload["tenant_id"] == str(tenant_id)
    assert payload["company_id"] == str(company_id)
    assert payload["invite_id"] == str(invite_id)
    assert _hash_token(token) != token
    assert len(_hash_token(token)) == 64


def test_ca_client_billing_calculates_invoice_totals() -> None:
    from decimal import Decimal

    from api.v1.ca_billing import CAInvoiceLineItem, _normalise_line_items

    items, subtotal, tax, total = _normalise_line_items(
        [
            CAInvoiceLineItem(
                description="Monthly compliance retainer",
                quantity=Decimal("1"),
                unit_price=Decimal("15000"),
                tax_rate_percent=Decimal("18"),
            ),
            CAInvoiceLineItem(
                description="Payroll PT filing",
                quantity=Decimal("2"),
                unit_price=Decimal("1000"),
                tax_rate_percent=Decimal("18"),
            ),
        ],
        default_tax_rate=Decimal("18"),
    )

    assert subtotal == Decimal("17000.00")
    assert tax == Decimal("3060.00")
    assert total == Decimal("20060.00")
    assert items[1]["amount"] == "2000.00"


def test_ca_004_005_006_routes_are_registered() -> None:
    from api.main import app

    route_paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/v1/professional-tax/states" in route_paths
    assert "/api/v1/professional-tax/returns/prepare" in route_paths
    assert "/api/v1/client-portal/invites" in route_paths
    assert "/api/v1/client-portal/public/accept" in route_paths
    assert "/api/v1/ca-billing/invoices" in route_paths
    assert "/api/v1/ca-billing/invoices/{invoice_id}/payments" in route_paths


@pytest.mark.asyncio
async def test_ca_capability_status_claims_ca_004_005_006_with_evidence() -> None:
    from api.v1.ca_operations import ca_capabilities_status

    result = await ca_capabilities_status(tenant_id="00000000-0000-0000-0000-000000000001")
    by_id = {item["id"]: item for item in result["items"]}

    assert by_id["professional_tax_portals"]["status"].startswith("available")
    assert by_id["client_portal"]["status"].startswith("available")
    assert by_id["ca_firm_billing"]["status"].startswith("available")
    assert by_id["professional_tax_portals"]["evidence"]
    assert by_id["client_portal"]["evidence"]
    assert by_id["ca_firm_billing"]["evidence"]
    assert by_id["traces_reconciliation"]["status"].startswith("available")
