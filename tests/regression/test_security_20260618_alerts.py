from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def test_ui_lockfile_uses_patched_security_alert_versions() -> None:
    lockfile = json.loads(Path("ui/package-lock.json").read_text(encoding="utf-8"))
    packages = lockfile["packages"]

    assert packages["node_modules/form-data"]["version"] == "4.0.6"
    assert packages["node_modules/@babel/core"]["version"] == "7.29.6"
    assert packages["node_modules/dompurify"]["version"] == "3.4.11"
    assert packages["node_modules/undici"]["version"] == "7.28.0"


@pytest.mark.asyncio
async def test_traces_reconciliation_does_not_echo_raw_validation_exception() -> None:
    from connectors.finance.traces import TracesConnector

    connector = TracesConnector({})
    result = await connector.reconcile_tds_with_traces(
        expected_deductions=[
            {
                "deductee_pan": "ABCDE1234F",
                "section": "194C",
                "tds_amount": "Traceback File C:/secret.py token=abc",
            }
        ],
        traces_statement=[
            {
                "deductee_pan": "ABCDE1234F",
                "section": "194C",
                "tds_amount": "100",
            }
        ],
    )

    error_text = result["row_errors"][0]["error"]
    assert error_text == "Row has an invalid numeric amount."
    assert "Traceback" not in error_text
    assert "File " not in error_text
    assert "secret.py" not in error_text
    assert "token=abc" not in error_text


@pytest.mark.asyncio
async def test_gstn_bulk_eway_bill_does_not_echo_raw_validation_exception() -> None:
    from connectors.finance.gstn import GstnConnector

    connector = GstnConnector({})
    result = await connector.bulk_generate_eway_bills(
        invoices=[
            {
                "supply_type": "outward",
                "sub_supply_type": "supply",
                "document_type": "tax invoice",
                "document_number": "INV-1",
                "document_date": "2026-06-18",
                "from_gstin": "29ABCDE1234F1Z5",
                "from_pin_code": "560001",
                "from_state_code": "29",
                "to_gstin": "27ABCDE1234F1Z5",
                "to_pin_code": "400001",
                "to_state_code": "27",
                "product_name": "Services",
                "hsn_code": "9983",
                "quantity": "1",
                "unit": "NOS",
                "taxable_amount": "Traceback File C:/secret.py token=abc",
                "total_invoice_value": "118",
                "transport_mode": "road",
                "distance_km": "10",
                "vehicle_number": "KA01AB1234",
            }
        ],
        submit=False,
    )

    error_text = result["failed"][0]["error"]
    assert error_text == "One or more numeric e-way bill fields are invalid."
    assert "Traceback" not in error_text
    assert "File " not in error_text
    assert "secret.py" not in error_text
    assert "token=abc" not in error_text


@pytest.mark.asyncio
async def test_ca_operations_route_boundary_redacts_raw_connector_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1.ca_operations import TracesReconcileRequest, reconcile_traces
    from connectors.finance.traces import TracesConnector

    async def raw_connector_result(self: TracesConnector, **params: Any) -> dict[str, Any]:
        return {
            "status": "needs_review",
            "summary": {"row_errors": 1},
            "row_errors": [
                {
                    "source": "books",
                    "row_number": 1,
                    "error": "Traceback (most recent call last): File C:/secret.py token=abc",
                }
            ],
        }

    monkeypatch.setattr(TracesConnector, "reconcile_tds_with_traces", raw_connector_result)

    result = await reconcile_traces(
        TracesReconcileRequest(
            expected_deductions=[{"deductee_pan": "ABCDE1234F"}],
            traces_statement=[{"deductee_pan": "ABCDE1234F"}],
        ),
        tenant_id="tenant-1",
    )

    error_text = result["row_errors"][0]["error"]
    assert error_text == "Row failed validation."
    assert result["row_errors"][0]["error_code"] == "row_validation_failed"
    assert "Traceback" not in error_text
    assert "File " not in error_text
    assert "secret.py" not in error_text
    assert "token=abc" not in error_text


@pytest.mark.asyncio
async def test_ca_operations_gstn_boundary_redacts_raw_connector_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1.ca_operations import EwayBillBulkRequest, bulk_generate_eway_bills
    from connectors.finance.gstn import GstnConnector

    async def raw_connector_result(self: GstnConnector, **params: Any) -> dict[str, Any]:
        return {
            "status": "completed_with_errors",
            "summary": {"failed": 1},
            "failed": [
                {
                    "row_number": 1,
                    "client_reference": "INV-1",
                    "error": "Traceback (most recent call last): File C:/secret.py token=abc",
                }
            ],
        }

    monkeypatch.setattr(GstnConnector, "bulk_generate_eway_bills", raw_connector_result)

    result = await bulk_generate_eway_bills(
        EwayBillBulkRequest(invoices=[{"document_number": "INV-1"}]),
        tenant_id="tenant-1",
    )

    error_text = result["failed"][0]["error"]
    assert error_text == "Row failed validation."
    assert result["failed"][0]["error_code"] == "row_validation_failed"
    assert "Traceback" not in error_text
    assert "File " not in error_text
    assert "secret.py" not in error_text
    assert "token=abc" not in error_text
