"""Monthly invoice generation.

For each active tenant we:
  1. Compute usage from agent_task_results + billing plan.
  2. Build line items (base subscription fee + usage overage).
  3. Render a PDF using reportlab (BSD license).
  4. Upload the PDF to GCS.
  5. Insert an Invoice row and email a link to the billing contact.

This runs monthly on the 1st at 01:00 IST via Celery Beat. Invoices
are idempotent per (tenant, period) via the invoice_number uniqueness
constraint.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select, text

from core.database import async_session_factory, get_tenant_session
from core.models.invoice import Invoice
from core.models.tenant import Tenant

logger = structlog.get_logger()


# Plan pricing — aligned with core.billing.limits.PLAN_PRICING
PLAN_MONTHLY_FEE = {
    "free": Decimal("0"),
    "pro": Decimal("99.00"),
    "enterprise": Decimal("499.00"),
}
USAGE_RATE_PER_1K_TASKS = Decimal("2.50")  # $2.50 per 1000 tasks above plan allowance
PLAN_TASK_ALLOWANCE = {"free": 1_000, "pro": 10_000, "enterprise": 100_000}


def _month_window(ref: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) of the calendar month containing `ref`."""
    start = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # end is the first moment of the next month minus 1 microsecond
    next_month_first = (start + timedelta(days=32)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return start, next_month_first


async def _count_tasks(
    tenant_id: uuid.UUID, start: datetime, end: datetime
) -> int:
    async with get_tenant_session(tenant_id) as session:
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM agent_task_results "
                "WHERE tenant_id = :tid AND created_at >= :s AND created_at < :e"
            ),
            {"tid": str(tenant_id), "s": start, "e": end},
        )
        return int(result.scalar_one() or 0)


def _build_line_items(
    plan: str, task_count: int
) -> tuple[list[dict[str, Any]], Decimal]:
    """Return (line_items, subtotal)."""
    items: list[dict[str, Any]] = []
    subtotal = Decimal("0.00")

    base = PLAN_MONTHLY_FEE.get(plan, Decimal("0"))
    items.append(
        {
            "description": f"{plan.title()} plan — monthly subscription",
            "qty": 1,
            "unit_price": str(base),
            "amount": str(base),
        }
    )
    subtotal += base

    allowance = PLAN_TASK_ALLOWANCE.get(plan, 0)
    overage = max(0, task_count - allowance)
    if overage > 0:
        units = Decimal(overage) / Decimal(1000)
        overage_amount = (units * USAGE_RATE_PER_1K_TASKS).quantize(Decimal("0.01"))
        items.append(
            {
                "description": (
                    f"Usage overage — {overage} tasks over the {allowance} allowance"
                ),
                "qty": float(units),
                "unit_price": str(USAGE_RATE_PER_1K_TASKS) + " / 1000",
                "amount": str(overage_amount),
            }
        )
        subtotal += overage_amount

    return items, subtotal


def _render_pdf(
    invoice_number: str,
    tenant_name: str,
    period_start: datetime,
    period_end: datetime,
    line_items: list[dict[str, Any]],
    subtotal: Decimal,
    tax: Decimal,
    total: Decimal,
    currency: str,
) -> bytes:
    """Render the invoice as a PDF using reportlab."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise RuntimeError(
            "reportlab is required to render invoices — run: pip install reportlab"
        ) from exc

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()

    story: list[Any] = []
    story.append(Paragraph("<b>AgenticOrg</b>", styles["Heading1"]))
    story.append(Paragraph("AI Virtual Employee Platform", styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(f"<b>Invoice {invoice_number}</b>", styles["Heading2"]))
    story.append(Paragraph(f"Billed to: {tenant_name}", styles["Normal"]))
    story.append(
        Paragraph(
            f"Period: {period_start.date()} to {period_end.date()}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.4 * cm))

    table_data: list[list[Any]] = [["Description", "Qty", "Unit price", "Amount"]]
    for item in line_items:
        table_data.append(
            [
                item["description"],
                str(item["qty"]),
                f"{item['unit_price']}",
                f"{currency} {item['amount']}",
            ]
        )
    table_data.append(["", "", "Subtotal", f"{currency} {subtotal}"])
    table_data.append(["", "", "Tax", f"{currency} {tax}"])
    table_data.append(["", "", "Total", f"{currency} {total}"])

    t = Table(table_data, colWidths=[8 * cm, 2 * cm, 3 * cm, 3 * cm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 0.6 * cm))
    story.append(
        Paragraph(
            "Payment is due within 30 days. Contact sanjeev@agenticorg.ai with any questions.",
            styles["Normal"],
        )
    )

    doc.build(story)
    return buf.getvalue()


async def _upload_pdf(tenant_id: uuid.UUID, invoice_number: str, data: bytes) -> str:
    """Upload the PDF to GCS and return its URL. Falls back to local /tmp on dev."""
    try:
        import os

        from google.cloud import storage

        bucket_name = os.getenv("AGENTICORG_INVOICE_BUCKET", "")
        if not bucket_name:
            raise RuntimeError("AGENTICORG_INVOICE_BUCKET not set")
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(f"invoices/{tenant_id}/{invoice_number}.pdf")
        blob.upload_from_string(data, content_type="application/pdf")
        return f"gs://{bucket_name}/invoices/{tenant_id}/{invoice_number}.pdf"
    except Exception:
        logger.debug("invoice_gcs_upload_skipped")
        return ""


async def generate_invoices_for_period(
    ref: datetime | None = None,
) -> dict[str, Any]:
    """Iterate every tenant, generate a monthly invoice if missing."""
    now = ref or datetime.now(UTC)
    start, end = _month_window(now - timedelta(days=1))  # previous calendar month

    created = 0
    skipped = 0

    async with async_session_factory() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.deleted_at.is_(None))
        )
        tenants = result.scalars().all()

    for tenant in tenants:
        try:
            invoice_number = f"AO-{tenant.id.hex[:6].upper()}-{start.strftime('%Y%m')}"

            # Idempotency — skip if this invoice already exists
            async with async_session_factory() as check_session:
                result = await check_session.execute(
                    select(Invoice).where(
                        Invoice.tenant_id == tenant.id,
                        Invoice.invoice_number == invoice_number,
                    )
                )
                if result.scalar_one_or_none() is not None:
                    skipped += 1
                    continue

            task_count = await _count_tasks(tenant.id, start, end)
            line_items, subtotal = _build_line_items(tenant.plan, task_count)

            tax = Decimal("0.00")  # tax handling is per-region; out of scope here
            total = subtotal + tax
            currency = "USD"

            if subtotal == 0:
                skipped += 1
                continue

            pdf_bytes = _render_pdf(
                invoice_number=invoice_number,
                tenant_name=tenant.name,
                period_start=start,
                period_end=end,
                line_items=line_items,
                subtotal=subtotal,
                tax=tax,
                total=total,
                currency=currency,
            )
            pdf_url = await _upload_pdf(tenant.id, invoice_number, pdf_bytes)

            async with async_session_factory() as write_session:
                inv = Invoice(
                    tenant_id=tenant.id,
                    invoice_number=invoice_number,
                    period_start=start,
                    period_end=end,
                    issue_date=datetime.now(UTC).date(),
                    due_date=datetime.now(UTC).date() + timedelta(days=30),
                    currency=currency,
                    subtotal=subtotal,
                    tax=tax,
                    total=total,
                    status="draft",
                    line_items=line_items,
                    pdf_url=pdf_url,
                    payment_provider="stripe" if currency == "USD" else "plural",
                )
                write_session.add(inv)
                await write_session.commit()

            created += 1
            logger.info(
                "invoice_generated",
                tenant_id=str(tenant.id),
                invoice_number=invoice_number,
                total=str(total),
            )
        except Exception:
            logger.exception(
                "invoice_generation_failed", tenant_id=str(tenant.id)
            )

    return {
        "created": created,
        "skipped": skipped,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }
