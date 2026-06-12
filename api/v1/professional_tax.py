"""Professional Tax APIs for CA firm client compliance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, select

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from connectors.finance.professional_tax import ProfessionalTaxConnector
from core.database import get_tenant_session
from core.models.company import Company
from core.models.professional_tax import ProfessionalTaxRegistration, ProfessionalTaxReturn

router = APIRouter(prefix="/professional-tax", tags=["Professional Tax"])


class PTRegistrationIn(BaseModel):
    state_code: str = Field(..., min_length=2, max_length=2)
    registration_number: str = Field(..., min_length=5, max_length=100)
    employer_name: str | None = Field(None, max_length=255)
    portal_username: str | None = Field(None, max_length=255)
    credential_ref: str | None = Field(None, max_length=500)
    status: str = Field("active", max_length=30)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("state_code")
    @classmethod
    def _upper_state(cls, value: str) -> str:
        return value.strip().upper()


class PTPrepareRequest(BaseModel):
    company_id: uuid.UUID
    state_code: str = Field(..., min_length=2, max_length=2)
    filing_period: str = Field(..., min_length=4, max_length=20)
    registration_number: str | None = Field(None, min_length=5, max_length=100)
    employer_name: str | None = Field(None, max_length=255)
    employees: list[dict[str, Any]]
    slabs: list[dict[str, Any]] = Field(default_factory=list)
    interest: Decimal = Decimal("0.00")
    penalty: Decimal = Decimal("0.00")
    notes: str | None = Field(None, max_length=1000)

    @field_validator("state_code")
    @classmethod
    def _upper_state(cls, value: str) -> str:
        return value.strip().upper()


class PTSubmitRequest(BaseModel):
    submit_to_portal: bool = False
    challan_number: str | None = Field(None, max_length=100)
    acknowledgement_number: str | None = Field(None, max_length=100)
    portal_response: dict[str, Any] = Field(default_factory=dict)


class PTRegistrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    state_code: str
    registration_number: str
    employer_name: str | None
    portal_username: str | None
    credential_ref: str | None
    status: str
    last_verified_at: datetime | None
    metadata_: dict[str, Any] = Field(alias="metadata")
    created_at: datetime


class PTReturnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    registration_id: uuid.UUID | None
    state_code: str
    filing_period: str
    employer_name: str | None
    employee_count: int
    gross_wages: Decimal
    pt_amount: Decimal
    interest: Decimal
    penalty: Decimal
    total_payable: Decimal
    status: str
    challan_number: str | None
    acknowledgement_number: str | None
    line_items: list[dict[str, Any]]
    payload: dict[str, Any]
    portal_response: dict[str, Any]
    notes: str | None
    created_at: datetime
    submitted_at: datetime | None


async def _load_company_or_404(session, tenant_uuid: uuid.UUID, company_id: uuid.UUID) -> Company:
    result = await session.execute(
        select(Company).where(Company.tenant_id == tenant_uuid, Company.id == company_id)
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(404, "Company not found")
    return company


def _pt_registration_out(row: ProfessionalTaxRegistration) -> PTRegistrationOut:
    return PTRegistrationOut.model_validate(
        {
            "id": row.id,
            "company_id": row.company_id,
            "state_code": row.state_code,
            "registration_number": row.registration_number,
            "employer_name": row.employer_name,
            "portal_username": row.portal_username,
            "credential_ref": row.credential_ref,
            "status": row.status,
            "last_verified_at": row.last_verified_at,
            "metadata": row.metadata_ or {},
            "created_at": row.created_at,
        }
    )


def _pt_return_out(row: ProfessionalTaxReturn) -> PTReturnOut:
    return PTReturnOut.model_validate(row)


@router.get("/states")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="professional_tax.states.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="professional_tax.states.read",
)
async def list_professional_tax_states(
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    connector = ProfessionalTaxConnector({})
    return await connector.list_professional_tax_states()


@router.get("/registrations", response_model=list[PTRegistrationOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="professional_tax.registrations.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="professional_tax.registrations.list",
)
async def list_pt_registrations(
    company_id: uuid.UUID | None = Query(None),
    tenant_id: str = Depends(get_current_tenant),
) -> list[PTRegistrationOut]:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        stmt = select(ProfessionalTaxRegistration).where(
            ProfessionalTaxRegistration.tenant_id == tenant_uuid
        )
        if company_id:
            stmt = stmt.where(ProfessionalTaxRegistration.company_id == company_id)
        rows = (await session.execute(stmt.order_by(ProfessionalTaxRegistration.state_code))).scalars().all()
        return [_pt_registration_out(row) for row in rows]


@router.put(
    "/companies/{company_id}/registrations/{state_code}",
    response_model=PTRegistrationOut,
    dependencies=[require_tenant_admin],
)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="professional_tax.registrations.write",
    rate_limit="standard",
    idempotency="tenant-company-state-upsert",
    audit_event="professional_tax.registrations.upsert",
)
async def upsert_pt_registration(
    company_id: uuid.UUID,
    state_code: str,
    body: PTRegistrationIn,
    tenant_id: str = Depends(get_current_tenant),
) -> PTRegistrationOut:
    tenant_uuid = uuid.UUID(tenant_id)
    state = state_code.strip().upper()
    if state != body.state_code:
        raise HTTPException(400, "state_code in path and body must match")
    connector = ProfessionalTaxConnector({})
    validation = await connector.validate_professional_tax_registration(
        state_code=state,
        registration_number=body.registration_number,
    )
    if validation["status"] != "valid":
        raise HTTPException(422, {"errors": validation["errors"]})

    async with get_tenant_session(tenant_uuid) as session:
        await _load_company_or_404(session, tenant_uuid, company_id)
        result = await session.execute(
            select(ProfessionalTaxRegistration).where(
                ProfessionalTaxRegistration.tenant_id == tenant_uuid,
                ProfessionalTaxRegistration.company_id == company_id,
                ProfessionalTaxRegistration.state_code == state,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ProfessionalTaxRegistration(
                tenant_id=tenant_uuid,
                company_id=company_id,
                state_code=state,
                registration_number=body.registration_number.strip(),
                employer_name=body.employer_name,
                portal_username=body.portal_username,
                credential_ref=body.credential_ref,
                status=body.status,
                metadata_=body.metadata,
            )
            session.add(row)
        else:
            row.registration_number = body.registration_number.strip()
            row.employer_name = body.employer_name
            row.portal_username = body.portal_username
            row.credential_ref = body.credential_ref
            row.status = body.status
            row.metadata_ = body.metadata
            row.updated_at = datetime.now(UTC)
        await session.flush()
        return _pt_registration_out(row)


@router.post("/returns/prepare", response_model=PTReturnOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="professional_tax.returns.prepare",
    rate_limit="standard",
    idempotency="tenant-company-state-period-upsert",
    audit_event="professional_tax.returns.prepare",
)
async def prepare_pt_return(
    body: PTPrepareRequest,
    tenant_id: str = Depends(get_current_tenant),
    current_user: dict = Depends(get_current_user),
) -> PTReturnOut:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        company = await _load_company_or_404(session, tenant_uuid, body.company_id)
        reg_result = await session.execute(
            select(ProfessionalTaxRegistration).where(
                ProfessionalTaxRegistration.tenant_id == tenant_uuid,
                ProfessionalTaxRegistration.company_id == body.company_id,
                ProfessionalTaxRegistration.state_code == body.state_code,
            )
        )
        registration = reg_result.scalar_one_or_none()
        registration_number = body.registration_number or (
            registration.registration_number if registration else None
        )
        if not registration_number:
            raise HTTPException(
                422,
                "Professional Tax registration must be saved or registration_number supplied.",
            )
        connector = ProfessionalTaxConnector({})
        try:
            prepared = await connector.prepare_professional_tax_return(
                state_code=body.state_code,
                filing_period=body.filing_period,
                registration_number=registration_number,
                employer_name=body.employer_name or company.name,
                employees=body.employees,
                slabs=body.slabs,
                interest=str(body.interest),
                penalty=str(body.penalty),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if prepared["status"] != "ready_for_filing":
            raise HTTPException(422, prepared)
        payload = prepared["payload"]
        result = await session.execute(
            select(ProfessionalTaxReturn).where(
                ProfessionalTaxReturn.tenant_id == tenant_uuid,
                ProfessionalTaxReturn.company_id == body.company_id,
                ProfessionalTaxReturn.state_code == body.state_code,
                ProfessionalTaxReturn.filing_period == body.filing_period,
            )
        )
        row = result.scalar_one_or_none()
        values = {
            "registration_id": registration.id if registration else None,
            "employer_name": payload["employer_name"],
            "employee_count": payload["employee_count"],
            "gross_wages": Decimal(payload["gross_wages"]),
            "pt_amount": Decimal(payload["pt_amount"]),
            "interest": Decimal(payload["interest"]),
            "penalty": Decimal(payload["penalty"]),
            "total_payable": Decimal(payload["total_payable"]),
            "status": "ready",
            "line_items": payload["line_items"],
            "payload": payload,
            "portal_response": {},
            "prepared_by": current_user.get("email") or current_user.get("sub"),
            "notes": body.notes,
            "updated_at": datetime.now(UTC),
        }
        if row is None:
            row = ProfessionalTaxReturn(
                tenant_id=tenant_uuid,
                company_id=body.company_id,
                state_code=body.state_code,
                filing_period=body.filing_period,
                **values,
            )
            session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        await session.flush()
        return _pt_return_out(row)


@router.get("/returns", response_model=list[PTReturnOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="professional_tax.returns.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="professional_tax.returns.list",
)
async def list_pt_returns(
    company_id: uuid.UUID | None = Query(None),
    state_code: str | None = Query(None),
    tenant_id: str = Depends(get_current_tenant),
) -> list[PTReturnOut]:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        stmt = select(ProfessionalTaxReturn).where(ProfessionalTaxReturn.tenant_id == tenant_uuid)
        if company_id:
            stmt = stmt.where(ProfessionalTaxReturn.company_id == company_id)
        if state_code:
            stmt = stmt.where(ProfessionalTaxReturn.state_code == state_code.strip().upper())
        rows = (await session.execute(stmt.order_by(desc(ProfessionalTaxReturn.created_at)))).scalars().all()
        return [_pt_return_out(row) for row in rows]


@router.post(
    "/returns/{return_id}/submit",
    response_model=PTReturnOut,
    dependencies=[require_tenant_admin],
)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="professional_tax.returns.submit",
    rate_limit="compliance-mutating",
    idempotency="pt-return-submit-or-ack",
    audit_event="professional_tax.returns.submit",
)
async def submit_pt_return(
    return_id: uuid.UUID,
    body: PTSubmitRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> PTReturnOut:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        result = await session.execute(
            select(ProfessionalTaxReturn).where(
                ProfessionalTaxReturn.tenant_id == tenant_uuid,
                ProfessionalTaxReturn.id == return_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Professional Tax return not found")

        if body.submit_to_portal:
            connector = ProfessionalTaxConnector({})
            submitted = await connector.submit_professional_tax_return(
                state_code=row.state_code,
                payload=row.payload,
                dry_run=False,
            )
            if submitted.get("status") == "not_connected":
                raise HTTPException(409, submitted)
            row.portal_response = submitted
            row.status = "submitted"
            row.submitted_at = datetime.now(UTC)
        else:
            if body.challan_number or body.acknowledgement_number:
                row.status = "submitted"
                row.challan_number = body.challan_number
                row.acknowledgement_number = body.acknowledgement_number
                row.portal_response = body.portal_response
                row.submitted_at = datetime.now(UTC)
            else:
                row.status = "ready_for_manual_upload"
                row.portal_response = {
                    "status": "ready_for_manual_upload",
                    "reason": "No challan or acknowledgement supplied yet.",
                }
        row.updated_at = datetime.now(UTC)
        await session.flush()
        return _pt_return_out(row)
