"""CA-firm operations endpoints.

These routes expose CA workflows that are not generic connector CRUD:
TRACES reconciliation, e-way bill batch preparation, and explicit capability
status for CA-firm modules. The status route is intentionally honest about
remaining live-credential dependencies so demos cannot imply unsupported
portal success.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from connectors.finance.gstn import GstnConnector
from connectors.finance.traces import TracesConnector

router = APIRouter()


class TracesReconcileRequest(BaseModel):
    expected_deductions: list[dict[str, Any]] = Field(default_factory=list)
    traces_statement: list[dict[str, Any]] = Field(default_factory=list)
    tolerance_rupees: float = Field(1.0, ge=0)


class EwayBillBulkRequest(BaseModel):
    invoices: list[dict[str, Any]] = Field(default_factory=list)
    submit_to_gstn: bool = False


def _safe_ca_row_error_message(value: Any) -> str:
    if not isinstance(value, str):
        return "Row failed validation."

    allowed_prefixes = (
        "Missing required e-way bill fields:",
        "Row is missing or has invalid required fields:",
    )
    allowed_messages = {
        "E-way bill row failed validation.",
        "One or more numeric e-way bill fields are invalid.",
        "Row failed validation.",
        "Row has an invalid numeric amount.",
        "rows must be a list of objects",
    }
    if value in allowed_messages or value.startswith(allowed_prefixes):
        return value
    return "Row failed validation."


def _sanitize_ca_error_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return {"error": "Row failed validation.", "error_code": "row_validation_failed"}

    sanitized = dict(item)
    if "error" in sanitized:
        sanitized["error"] = _safe_ca_row_error_message(sanitized["error"])
        sanitized.setdefault("error_code", "row_validation_failed")
    return sanitized


def _sanitize_ca_operation_result(result: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(result)
    for bucket in ("errors", "failed", "row_errors"):
        entries = sanitized.get(bucket)
        if isinstance(entries, list):
            sanitized[bucket] = [_sanitize_ca_error_item(item) for item in entries]
    return sanitized


@router.post(
    "/connectors/traces/reconcile",
    dependencies=[require_tenant_admin],
)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.traces.reconcile",
    rate_limit="connector-bulk",
    idempotency="deterministic-upload-reconciliation",
    audit_event="connectors.traces.reconcile",
)
async def reconcile_traces(
    body: TracesReconcileRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Reconcile books-side TDS rows with downloaded TRACES statement rows."""
    if not body.expected_deductions:
        raise HTTPException(422, "expected_deductions must contain at least one row")
    if not body.traces_statement:
        raise HTTPException(422, "traces_statement must contain at least one row")

    connector = TracesConnector({})
    result = await connector.reconcile_tds_with_traces(
        expected_deductions=body.expected_deductions,
        traces_statement=body.traces_statement,
        tolerance_rupees=body.tolerance_rupees,
    )
    return _sanitize_ca_operation_result(result)


@router.post(
    "/connectors/gstn/eway-bills/bulk-generate",
    dependencies=[require_tenant_admin],
)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.gstn.eway.bulk_generate",
    rate_limit="connector-bulk",
    idempotency="deterministic-bulk-payload-generation",
    audit_event="connectors.gstn.eway.bulk_generate",
)
async def bulk_generate_eway_bills(
    body: EwayBillBulkRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Validate and prepare a batch of GSTN e-way bill payloads.

    Live GSTN submission is deliberately not faked from this route. The route
    prepares provider-valid payloads and reports row-level validation errors;
    live submission must use a configured encrypted GSTN connector credential.
    """
    if not body.invoices:
        raise HTTPException(422, "invoices must contain at least one row")
    if body.submit_to_gstn:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "gstn_credentials_required",
                "message": (
                    "Bulk e-way bill validation is available here. Live GSTN "
                    "submission must run through a configured encrypted GSTN "
                    "connector credential; this route will not fake portal success."
                ),
            },
        )

    connector = GstnConnector({})
    result = await connector.bulk_generate_eway_bills(
        invoices=body.invoices,
        source="bulk_upload",
        submit=False,
    )
    return _sanitize_ca_operation_result(result)


@router.get("/ca-capabilities/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ca_capabilities.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="ca_capabilities.status.read",
)
async def ca_capabilities_status(
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Return shipped vs roadmap status for CA-firm workflows."""
    return {
        "items": [
            {
                "id": "traces_reconciliation",
                "label": "TRACES TDS reconciliation",
                "status": "available_offline_reconciliation",
                "evidence": [
                    "TRACES connector registered",
                    "POST /api/v1/connectors/traces/reconcile",
                    "CA Operations UI",
                ],
                "residual": "Live portal download still requires configured TRACES automation credentials.",
            },
            {
                "id": "eway_bill_bulk",
                "label": "GSTN e-way bill bulk preparation",
                "status": "available_validation_payload_generation",
                "evidence": [
                    "GSTN connector validates Part A and Part B",
                    "POST /api/v1/connectors/gstn/eway-bills/bulk-generate",
                    "Row-level import errors",
                ],
                "residual": (
                    "Live GSTN submission is fail-closed until encrypted GSTN credentials "
                    "are wired to the batch route."
                ),
            },
            {
                "id": "professional_tax_portals",
                "label": "Professional Tax state portal filing",
                "status": "available_draft_and_manual_acknowledgement",
                "evidence": [
                    "Professional Tax connector registered",
                    "GET /api/v1/professional-tax/states",
                    "POST /api/v1/professional-tax/returns/prepare",
                    "POST /api/v1/professional-tax/returns/{return_id}/submit",
                ],
                "residual": (
                    "Live state portal filing requires per-state credentials; slab rates "
                    "must come from firm-maintained compliance rules or payroll deductions."
                ),
            },
            {
                "id": "client_portal",
                "label": "Client-facing portal",
                "status": "available_invite_token_portal",
                "evidence": [
                    "POST /api/v1/client-portal/invites",
                    "POST /api/v1/client-portal/documents",
                    "POST /api/v1/client-portal/public/accept",
                    "POST /api/v1/client-portal/public/dashboard",
                ],
                "residual": "Portal access is invite-token based; SSO and custom-domain polish can be layered later.",
            },
            {
                "id": "ca_firm_billing",
                "label": "CA-firm client billing",
                "status": "available_invoice_and_payment_tracking",
                "evidence": [
                    "POST /api/v1/ca-billing/service-plans",
                    "POST /api/v1/ca-billing/invoices",
                    "POST /api/v1/ca-billing/invoices/{invoice_id}/send",
                    "POST /api/v1/ca-billing/invoices/{invoice_id}/payments",
                ],
                "residual": (
                    "Payment gateway collection links are not yet wired; staff can issue "
                    "invoices and record payments."
                ),
            },
        ],
    }
