"""CA firm client billing APIs."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.models.ca_client_billing import CAClientInvoice, CAClientPayment, CAServicePlan
from core.models.client_portal import ClientPortalDocument
from core.models.company import Company

router = APIRouter(prefix="/ca-billing", tags=["CA Client Billing"])

MONEY = Decimal("0.01")


class CAServicePlanIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    billing_cycle: str = Field("monthly", max_length=20)
    currency: str = Field("INR", min_length=3, max_length=3)
    default_fee: Decimal = Field(..., ge=0)
    tax_rate_percent: Decimal = Field(Decimal("18.000"), ge=0, le=100)
    active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, value: str) -> str:
        return value.upper()


class CAServicePlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    billing_cycle: str
    currency: str
    default_fee: Decimal
    tax_rate_percent: Decimal
    active: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CAInvoiceLineItem(BaseModel):
    description: str = Field(..., min_length=1, max_length=255)
    quantity: Decimal = Field(Decimal("1"), gt=0)
    unit_price: Decimal = Field(..., ge=0)
    tax_rate_percent: Decimal | None = Field(None, ge=0, le=100)


class CAClientInvoiceCreate(BaseModel):
    company_id: uuid.UUID
    service_plan_id: uuid.UUID | None = None
    invoice_number: str | None = Field(None, max_length=60)
    issue_date: date = Field(default_factory=lambda: datetime.now(UTC).date())
    due_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    currency: str = Field("INR", min_length=3, max_length=3)
    line_items: list[CAInvoiceLineItem] = Field(default_factory=list)
    tax_rate_percent: Decimal = Field(Decimal("18.000"), ge=0, le=100)
    notes: str | None = Field(None, max_length=2000)
    send_immediately: bool = False

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, value: str) -> str:
        return value.upper()


class CAInvoiceStatusUpdate(BaseModel):
    status: str = Field(..., max_length=30)


class CAClientPaymentCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    payment_date: date = Field(default_factory=lambda: datetime.now(UTC).date())
    method: str = Field(..., min_length=1, max_length=40)
    reference: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=1000)


class CAClientPaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    company_id: uuid.UUID
    amount: Decimal
    payment_date: date
    method: str
    reference: str | None
    notes: str | None
    recorded_by: str | None
    created_at: datetime


class CAClientInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    service_plan_id: uuid.UUID | None
    invoice_number: str
    issue_date: date
    due_date: date
    period_start: date | None
    period_end: date | None
    currency: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    amount_paid: Decimal
    balance_due: Decimal
    status: str
    line_items: list[dict[str, Any]]
    notes: str | None
    sent_at: datetime | None
    paid_at: datetime | None
    created_by: str | None
    created_at: datetime


def _money(value: Any, *, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a numeric amount") from exc
    if amount < 0:
        raise ValueError(f"{field} cannot be negative")
    return amount


def _normalise_line_items(
    line_items: list[CAInvoiceLineItem],
    *,
    default_tax_rate: Decimal,
) -> tuple[list[dict[str, Any]], Decimal, Decimal, Decimal]:
    if not line_items:
        raise ValueError("line_items must contain at least one billing item")
    normalised: list[dict[str, Any]] = []
    subtotal = Decimal("0.00")
    tax = Decimal("0.00")
    for item in line_items:
        quantity = _money(item.quantity, field="quantity")
        unit_price = _money(item.unit_price, field="unit_price")
        tax_rate = Decimal(str(item.tax_rate_percent if item.tax_rate_percent is not None else default_tax_rate))
        amount = (quantity * unit_price).quantize(MONEY, rounding=ROUND_HALF_UP)
        item_tax = (amount * tax_rate / Decimal("100")).quantize(MONEY, rounding=ROUND_HALF_UP)
        subtotal += amount
        tax += item_tax
        normalised.append({
            "description": item.description,
            "quantity": str(quantity),
            "unit_price": str(unit_price),
            "amount": str(amount),
            "tax_rate_percent": str(tax_rate),
            "tax": str(item_tax),
        })
    total = (subtotal + tax).quantize(MONEY, rounding=ROUND_HALF_UP)
    return normalised, subtotal.quantize(MONEY), tax.quantize(MONEY), total


async def _load_company_or_404(session, tenant_uuid: uuid.UUID, company_id: uuid.UUID) -> Company:
    result = await session.execute(
        select(Company).where(Company.tenant_id == tenant_uuid, Company.id == company_id)
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(404, "Company not found")
    return company


async def _next_invoice_number(session, tenant_uuid: uuid.UUID, issue_date: date) -> str:
    prefix = f"CA-{issue_date:%Y%m}"
    count = await session.scalar(
        select(func.count())
        .select_from(CAClientInvoice)
        .where(
            CAClientInvoice.tenant_id == tenant_uuid,
            CAClientInvoice.invoice_number.like(f"{prefix}-%"),
        )
    )
    return f"{prefix}-{int(count or 0) + 1:04d}"


def _service_plan_out(row: CAServicePlan) -> CAServicePlanOut:
    return CAServicePlanOut(
        id=row.id,
        name=row.name,
        description=row.description,
        billing_cycle=row.billing_cycle,
        currency=row.currency,
        default_fee=row.default_fee,
        tax_rate_percent=row.tax_rate_percent,
        active=row.active,
        metadata=row.metadata_ or {},
        created_at=row.created_at,
    )


@router.get("/service-plans", response_model=list[CAServicePlanOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ca_billing.service_plans.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="ca_billing.service_plans.list",
)
async def list_service_plans(
    include_inactive: bool = False,
    tenant_id: str = Depends(get_current_tenant),
) -> list[CAServicePlanOut]:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        stmt = select(CAServicePlan).where(CAServicePlan.tenant_id == tenant_uuid)
        if not include_inactive:
            stmt = stmt.where(CAServicePlan.active.is_(True))
        rows = (await session.execute(stmt.order_by(CAServicePlan.name))).scalars().all()
        return [_service_plan_out(row) for row in rows]


@router.post("/service-plans", response_model=CAServicePlanOut, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ca_billing.service_plans.write",
    rate_limit="standard",
    idempotency="tenant-service-plan-create",
    audit_event="ca_billing.service_plans.create",
)
async def create_service_plan(
    body: CAServicePlanIn,
    tenant_id: str = Depends(get_current_tenant),
) -> CAServicePlanOut:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        row = CAServicePlan(
            tenant_id=tenant_uuid,
            name=body.name,
            description=body.description,
            billing_cycle=body.billing_cycle,
            currency=body.currency,
            default_fee=_money(body.default_fee, field="default_fee"),
            tax_rate_percent=Decimal(str(body.tax_rate_percent)),
            active=body.active,
            metadata_=body.metadata,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise HTTPException(409, "Service plan name already exists for this tenant") from exc
        return _service_plan_out(row)


@router.get("/invoices", response_model=list[CAClientInvoiceOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ca_billing.invoices.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="ca_billing.invoices.list",
)
async def list_ca_client_invoices(
    company_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> list[CAClientInvoiceOut]:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        stmt = select(CAClientInvoice).where(CAClientInvoice.tenant_id == tenant_uuid)
        if company_id:
            stmt = stmt.where(CAClientInvoice.company_id == company_id)
        if status:
            stmt = stmt.where(CAClientInvoice.status == status)
        rows = (await session.execute(stmt.order_by(desc(CAClientInvoice.issue_date)))).scalars().all()
        return [CAClientInvoiceOut.model_validate(row) for row in rows]


@router.post("/invoices", response_model=CAClientInvoiceOut, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ca_billing.invoices.write",
    rate_limit="billing-mutating",
    idempotency="tenant-client-invoice-create",
    audit_event="ca_billing.invoices.create",
)
async def create_ca_client_invoice(
    body: CAClientInvoiceCreate,
    tenant_id: str = Depends(get_current_tenant),
    current_user: dict = Depends(get_current_user),
) -> CAClientInvoiceOut:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        await _load_company_or_404(session, tenant_uuid, body.company_id)
        service_plan: CAServicePlan | None = None
        items = body.line_items
        tax_rate = body.tax_rate_percent
        currency = body.currency
        if body.service_plan_id:
            plan_result = await session.execute(
                select(CAServicePlan).where(
                    CAServicePlan.tenant_id == tenant_uuid,
                    CAServicePlan.id == body.service_plan_id,
                    CAServicePlan.active.is_(True),
                )
            )
            service_plan = plan_result.scalar_one_or_none()
            if service_plan is None:
                raise HTTPException(404, "Active service plan not found")
            tax_rate = service_plan.tax_rate_percent
            currency = service_plan.currency
            if not items:
                items = [
                    CAInvoiceLineItem(
                        description=service_plan.name,
                        quantity=Decimal("1"),
                        unit_price=service_plan.default_fee,
                        tax_rate_percent=service_plan.tax_rate_percent,
                    )
                ]
        try:
            line_items, subtotal, tax, total = _normalise_line_items(items, default_tax_rate=tax_rate)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        due_date = body.due_date or body.issue_date + timedelta(days=15)
        invoice_number = body.invoice_number or await _next_invoice_number(session, tenant_uuid, body.issue_date)
        invoice = CAClientInvoice(
            tenant_id=tenant_uuid,
            company_id=body.company_id,
            service_plan_id=service_plan.id if service_plan else body.service_plan_id,
            invoice_number=invoice_number,
            issue_date=body.issue_date,
            due_date=due_date,
            period_start=body.period_start,
            period_end=body.period_end,
            currency=currency,
            subtotal=subtotal,
            tax=tax,
            total=total,
            amount_paid=Decimal("0.00"),
            balance_due=total,
            status="sent" if body.send_immediately else "draft",
            line_items=line_items,
            notes=body.notes,
            sent_at=datetime.now(UTC) if body.send_immediately else None,
            created_by=current_user.get("email") or current_user.get("sub"),
        )
        session.add(invoice)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise HTTPException(409, "Invoice number already exists for this tenant") from exc
        if body.send_immediately:
            await _publish_invoice_document(session, invoice, current_user)
        return CAClientInvoiceOut.model_validate(invoice)


async def _load_invoice_or_404(session, tenant_uuid: uuid.UUID, invoice_id: uuid.UUID) -> CAClientInvoice:
    result = await session.execute(
        select(CAClientInvoice).where(
            CAClientInvoice.tenant_id == tenant_uuid,
            CAClientInvoice.id == invoice_id,
        )
    )
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise HTTPException(404, "CA client invoice not found")
    return invoice


async def _publish_invoice_document(session, invoice: CAClientInvoice, current_user: dict) -> None:
    result = await session.execute(
        select(ClientPortalDocument).where(
            ClientPortalDocument.tenant_id == invoice.tenant_id,
            ClientPortalDocument.company_id == invoice.company_id,
            ClientPortalDocument.source_type == "ca_client_invoice",
            ClientPortalDocument.source_id == invoice.id,
        )
    )
    if result.scalar_one_or_none() is not None:
        return
    session.add(
        ClientPortalDocument(
            tenant_id=invoice.tenant_id,
            company_id=invoice.company_id,
            title=f"Invoice {invoice.invoice_number}",
            document_type="ca_client_invoice",
            filing_period=invoice.period_start.isoformat() if invoice.period_start else None,
            source_type="ca_client_invoice",
            source_id=invoice.id,
            visible_to_client=True,
            summary=f"{invoice.currency} {invoice.total} due by {invoice.due_date.isoformat()}",
            uploaded_by=current_user.get("email") or current_user.get("sub"),
            metadata_={
                "invoice_number": invoice.invoice_number,
                "status": invoice.status,
                "balance_due": str(invoice.balance_due),
            },
        )
    )


@router.post("/invoices/{invoice_id}/send", response_model=CAClientInvoiceOut, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ca_billing.invoices.send",
    rate_limit="billing-mutating",
    idempotency="invoice-send-once",
    audit_event="ca_billing.invoices.send",
)
async def send_ca_client_invoice(
    invoice_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
    current_user: dict = Depends(get_current_user),
) -> CAClientInvoiceOut:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        invoice = await _load_invoice_or_404(session, tenant_uuid, invoice_id)
        if invoice.status == "draft":
            invoice.status = "sent"
        invoice.sent_at = invoice.sent_at or datetime.now(UTC)
        invoice.updated_at = datetime.now(UTC)
        await _publish_invoice_document(session, invoice, current_user)
        await session.flush()
        return CAClientInvoiceOut.model_validate(invoice)


@router.post("/invoices/{invoice_id}/payments", response_model=CAClientInvoiceOut, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ca_billing.payments.write",
    rate_limit="billing-mutating",
    idempotency="invoice-payment-record",
    audit_event="ca_billing.payments.record",
)
async def record_ca_client_payment(
    invoice_id: uuid.UUID,
    body: CAClientPaymentCreate,
    tenant_id: str = Depends(get_current_tenant),
    current_user: dict = Depends(get_current_user),
) -> CAClientInvoiceOut:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        invoice = await _load_invoice_or_404(session, tenant_uuid, invoice_id)
        amount = _money(body.amount, field="amount")
        if amount > invoice.balance_due:
            raise HTTPException(422, "Payment amount cannot exceed invoice balance_due")
        payment = CAClientPayment(
            tenant_id=tenant_uuid,
            company_id=invoice.company_id,
            invoice_id=invoice.id,
            amount=amount,
            payment_date=body.payment_date,
            method=body.method,
            reference=body.reference,
            notes=body.notes,
            recorded_by=current_user.get("email") or current_user.get("sub"),
        )
        session.add(payment)
        invoice.amount_paid = (invoice.amount_paid + amount).quantize(MONEY)
        invoice.balance_due = (invoice.total - invoice.amount_paid).quantize(MONEY)
        if invoice.balance_due == Decimal("0.00"):
            invoice.status = "paid"
            invoice.paid_at = datetime.now(UTC)
        else:
            invoice.status = "part_paid"
        invoice.updated_at = datetime.now(UTC)
        await session.flush()
        return CAClientInvoiceOut.model_validate(invoice)


@router.patch("/invoices/{invoice_id}/status", response_model=CAClientInvoiceOut, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ca_billing.invoices.status.write",
    rate_limit="billing-mutating",
    idempotency="invoice-status-patch",
    audit_event="ca_billing.invoices.status",
)
async def update_ca_client_invoice_status(
    invoice_id: uuid.UUID,
    body: CAInvoiceStatusUpdate,
    tenant_id: str = Depends(get_current_tenant),
) -> CAClientInvoiceOut:
    allowed = {"draft", "sent", "part_paid", "paid", "overdue", "void"}
    if body.status not in allowed:
        raise HTTPException(422, f"status must be one of {', '.join(sorted(allowed))}")
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        invoice = await _load_invoice_or_404(session, tenant_uuid, invoice_id)
        invoice.status = body.status
        if body.status == "sent":
            invoice.sent_at = invoice.sent_at or datetime.now(UTC)
        if body.status == "paid":
            invoice.amount_paid = invoice.total
            invoice.balance_due = Decimal("0.00")
            invoice.paid_at = invoice.paid_at or datetime.now(UTC)
        invoice.updated_at = datetime.now(UTC)
        await session.flush()
        return CAClientInvoiceOut.model_validate(invoice)
