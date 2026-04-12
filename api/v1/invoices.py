"""Invoice API — list, fetch, manually trigger generation.

Routes:
  GET    /api/v1/billing/invoices
  GET    /api/v1/billing/invoices/{id}
  POST   /api/v1/billing/invoices/generate  (admin only)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select

from api.deps import get_current_tenant, require_tenant_admin
from core.database import async_session_factory
from core.models.invoice import Invoice

logger = structlog.get_logger()
router = APIRouter(prefix="/billing/invoices", tags=["Billing"], dependencies=[require_tenant_admin])


class InvoiceOut(BaseModel):
    id: uuid.UUID
    invoice_number: str
    period_start: datetime
    period_end: datetime
    issue_date: date
    due_date: date
    currency: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    status: str
    line_items: list[dict]
    pdf_url: str | None
    payment_provider: str | None


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(
    limit: int = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> list[InvoiceOut]:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(Invoice)
            .where(Invoice.tenant_id == tid)
            .order_by(desc(Invoice.period_start))
            .limit(limit)
        )
        rows = result.scalars().all()
        return [_to_out(r) for r in rows]


@router.get("/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(
    invoice_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
) -> InvoiceOut:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(Invoice).where(
                Invoice.tenant_id == tid, Invoice.id == invoice_id
            )
        )
        inv = result.scalar_one_or_none()
        if inv is None:
            raise HTTPException(404, "Invoice not found")
        return _to_out(inv)


@router.post("/generate")
async def generate_now(
    tenant_id: str = Depends(get_current_tenant),
) -> dict:
    """Manually trigger invoice generation for the caller's tenant only.

    Admin-only (enforced by the router-level require_tenant_admin).
    Scoped to a single tenant to prevent one admin from triggering a
    global invoice run.
    """
    import uuid

    from core.billing.invoice_generator import generate_invoices_for_period

    result = await generate_invoices_for_period(
        tenant_filter=uuid.UUID(tenant_id),
    )
    logger.info("invoice_manual_generate", tenant_id=tenant_id, **result)
    return result


def _to_out(inv: Invoice) -> InvoiceOut:
    return InvoiceOut(
        id=inv.id,
        invoice_number=inv.invoice_number,
        period_start=inv.period_start,
        period_end=inv.period_end,
        issue_date=inv.issue_date,
        due_date=inv.due_date,
        currency=inv.currency,
        subtotal=inv.subtotal,
        tax=inv.tax,
        total=inv.total,
        status=inv.status,
        line_items=inv.line_items or [],
        pdf_url=inv.pdf_url,
        payment_provider=inv.payment_provider,
    )
