# SPDX-License-Identifier: Apache-2.0
"""Provider invoices: one billing month's lines, imported whole from CSV or JSON, superseded and never deleted.

An invoice names a provider, its billing month (the provider's own calendar,
``core/spend/clock.py``), a reference and a currency; its lines come from a
bounded CSV or JSON file (``core/spend/imports.py``; a JSON object may hold
them under ``rows`` or ``lines``). A line has a kind: ``usage`` lines
(usage type required, amount zero or more) are compared with metered usage;
``credit`` (zero or less), ``tax``, ``fee`` and ``commitment`` lines are
reported beside them. A line's unit is a card unit of its usage type, or
empty for every unit of it; ``usage_date`` (a billing date inside the month)
marks a daily billing export.

An invoice is stored whole or not at all: a refused line refuses the import
(422 ``invoice_rejected`` with every refused line and its reason), because a
partial invoice would reconcile against a wrong total. A dry run reports
the same without writing. A second current invoice with the same provider,
month and reference is refused (409 ``invoice_exists``) unless ``replace``:
the earlier one is then ``superseded`` and points at its successor; both
are kept. Several references for one provider and month (two accounts) are
all current and are summed by the reconciliation. Each import is audited
(``spend.invoices.import``) in its own transaction.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select

from core.spend import audit, clock, imports, locks, vocab
from core.spend.errors import SpendError, require_actor

logger = structlog.get_logger()

IMPORT_REQUIRED = ("amount",)
IMPORT_OPTIONAL = ("line_kind", "usage_type", "model_sku", "unit", "quantity", "usage_date", "currency")
ENVELOPE_KEYS = ("rows", "lines")
MAX_LINE_AMOUNT = Decimal("1e12")
MAX_INVOICE_TOTAL = Decimal("1e13")
MAX_LINE_QUANTITY = Decimal("1e15")
INVOICE_REF_MAX = 128
MIN_YEAR = 2000
MAX_YEAR = 2999
LIST_LIMIT = 500
_REF_RE = re.compile(vocab.SKU_PATTERN)
_PERIOD_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def parse_period(text: Any) -> tuple[date, date]:
    """``"YYYY-MM"`` -> (its first day, the first day of the next month); 422 ``invalid_period`` otherwise.

    Years outside 2000 to 2999 are refused before any date is built, so no
    period can reach a date the calendar cannot hold.
    """
    match = _PERIOD_RE.fullmatch(str(text or "").strip())
    if not match or not MIN_YEAR <= int(match.group(1)) <= MAX_YEAR:
        raise SpendError(422, "invalid_period", f"a period is a month YYYY-MM of the years {MIN_YEAR} to {MAX_YEAR}")
    first = date(int(match.group(1)), int(match.group(2)), 1)
    return first, clock.next_month(first)


def period_text(period_start: date) -> str:
    return period_start.strftime("%Y-%m")


def check_provider(value: Any) -> str:
    """A provider that bills the tenant: in-house serving and platform storage have no provider invoice."""
    provider = vocab.norm_provider(value)
    if provider in vocab.IN_HOUSE_PROVIDERS or provider == vocab.STORAGE_PROVIDER:
        raise SpendError(422, "invalid_reference", "in-house serving and platform storage have no provider invoice")
    return provider


def check_invoice_ref(value: Any) -> str:
    """The provider's invoice or account reference: plain text of SKU characters, at most 128."""
    ref = vocab.free_text(value, field="invoice_ref", max_len=INVOICE_REF_MAX, required=True)
    if not _REF_RE.fullmatch(ref):
        raise SpendError(
            422,
            "invalid_text",
            "invoice_ref is 1 to 128 of A-Z a-z 0-9 space . _ : / @ - starting with a letter or digit",
        )
    return ref


def check_line(raw: dict[str, str], *, p0: date, p1: date, currency: str) -> dict[str, Any]:
    """One checked invoice line; ``SpendError`` (422) names what is wrong with it."""
    kind = vocab.choice(raw.get("line_kind") or "usage", vocab.LINE_KINDS, field="line_kind")
    amount = vocab.parse_decimal(
        raw.get("amount"), field="amount", minimum=-MAX_LINE_AMOUNT, maximum=MAX_LINE_AMOUNT, places=vocab.PRICE_PLACES
    )
    if kind == "usage" and amount < 0:
        raise SpendError(422, "invalid_number", "a usage line's amount is zero or more")
    if kind == "credit" and amount > 0:
        raise SpendError(422, "invalid_number", "a credit line's amount is zero or less")
    usage_type: str | None = None
    if raw.get("usage_type"):
        usage_type = vocab.choice(raw["usage_type"], vocab.USAGE_TYPES, field="usage_type")
    elif kind == "usage":
        raise SpendError(422, "invalid_value", "a usage line names its usage_type")
    unit: str | None = None
    if raw.get("unit"):
        if usage_type is None:
            raise SpendError(422, "invalid_unit", "a line with a unit names its usage_type")
        unit = vocab.choice(raw["unit"], vocab.CARD_UNITS[usage_type], field="unit", code="invalid_unit")
    quantity: Decimal | None = None
    if raw.get("quantity"):
        quantity = vocab.parse_decimal(
            raw["quantity"], field="quantity", minimum=0, maximum=MAX_LINE_QUANTITY, places=vocab.QUANTITY_PLACES
        )
    usage_date: date | None = None
    if raw.get("usage_date"):
        usage_date = vocab.parse_date(raw["usage_date"], field="usage_date")
        if not p0 <= usage_date < p1:
            raise SpendError(422, "invalid_period", "usage_date is a billing date inside the invoice's month")
    if raw.get("currency") and vocab.norm_currency(raw["currency"]) != currency:
        raise SpendError(422, "currency_mismatch", f"every line is in the invoice currency {currency}")
    return {
        "line_kind": kind,
        "usage_type": usage_type,
        "model_sku": vocab.norm_sku(raw.get("model_sku"), allow_empty=True),
        "unit": unit,
        "quantity": quantity,
        "amount": amount,
        "usage_date": usage_date,
    }


def check_lines(
    rows: Sequence[dict[str, str]], *, p0: date, p1: date, currency: str, report: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every line checked; a refused line is recorded in ``report`` (rows numbered from 2, as in the file)."""
    lines: list[dict[str, Any]] = []
    for index, raw in enumerate(rows, start=2):
        try:
            lines.append(check_line(raw, p0=p0, p1=p1, currency=currency))
        except SpendError as exc:
            imports.reject(report, row=index, key=f"line {index - 1}", reason=exc.code)
    return lines


def totals(lines: Sequence[dict[str, Any]]) -> tuple[Decimal, Decimal]:
    """(every line, the usage lines) summed; 422 ``invalid_number`` past the bounds of a stored total."""
    total = sum((line["amount"] for line in lines), Decimal("0"))
    usage = sum((line["amount"] for line in lines if line["line_kind"] == "usage"), Decimal("0"))
    if abs(total) > MAX_INVOICE_TOTAL or usage > MAX_INVOICE_TOTAL:
        raise SpendError(422, "invalid_number", f"an invoice totals at most {MAX_INVOICE_TOTAL} in magnitude")
    return total, usage


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def invoice_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "provider": row.provider,
        "period": period_text(row.period_start),
        "period_start": row.period_start.isoformat(),
        "invoice_ref": row.invoice_ref,
        "currency": str(row.currency).strip(),
        "total_amount": vocab.dec_str(row.total_amount),
        "usage_amount": vocab.dec_str(row.usage_amount),
        "line_count": int(row.line_count),
        "source": row.source,
        "file_sha256": str(row.file_sha256).strip(),
        "status": row.status,
        "superseded_by": str(row.superseded_by) if row.superseded_by else None,
        "imported_by": row.imported_by,
        "created_at": _iso(getattr(row, "created_at", None)),
    }


def line_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "line_no": int(row.line_no),
        "line_kind": row.line_kind,
        "usage_type": row.usage_type,
        "model_sku": row.model_sku or "",
        "unit": row.unit,
        "quantity": vocab.dec_str(row.quantity),
        "amount": vocab.dec_str(row.amount),
        "usage_date": _iso(row.usage_date),
    }


async def _current(session: Any, tenant_id: uuid.UUID, provider: str, period_start: date, ref: str) -> Any:
    from core.models.spend_invoice import SpendInvoice

    rows = (
        (
            await session.execute(
                select(SpendInvoice)
                .where(
                    SpendInvoice.tenant_id == tenant_id,
                    SpendInvoice.provider == provider,
                    SpendInvoice.period_start == period_start,
                    SpendInvoice.invoice_ref == ref,
                    SpendInvoice.status == "current",
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if rows else None


async def import_invoice(
    tenant_id: uuid.UUID,
    *,
    provider: str,
    period: str,
    invoice_ref: str,
    currency: str,
    path: Path,
    filename: str,
    content_type: str,
    actor: str,
    replace: bool,
    dry_run: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Import one invoice file; the report plus ``invoice_id``, ``total_amount``, ``usage_amount``, ``line_count``."""
    from core.database import get_tenant_session
    from core.models.spend_invoice import SpendInvoice, SpendInvoiceLine

    who = require_actor(actor)
    provider = check_provider(provider)
    p0, p1 = parse_period(period)
    ref = check_invoice_ref(invoice_ref)
    ccy = vocab.norm_currency(currency)
    stamp = now or clock.now_utc()
    rows = await asyncio.to_thread(
        imports.parse_rows,
        path,
        filename=filename,
        content_type=content_type,
        required=IMPORT_REQUIRED,
        optional=IMPORT_OPTIONAL,
        envelope_keys=ENVELOPE_KEYS,
    )
    if not rows:
        raise SpendError(400, "bad_file", "an invoice holds at least one line")
    digest = await asyncio.to_thread(imports.file_sha256, path)
    report = imports.new_report(dry_run=dry_run, received=len(rows))
    lines = check_lines(rows, p0=p0, p1=p1, currency=ccy, report=report)
    total, usage = totals(lines)
    out: dict[str, Any] = {
        **report,
        "provider": provider,
        "period": period_text(p0),
        "invoice_ref": ref,
        "currency": ccy,
        "invoice_id": None,
        "superseded_id": None,
        "total_amount": vocab.dec_str(total),
        "usage_amount": vocab.dec_str(usage),
        "line_count": len(lines),
    }
    if report["rejected"]:
        if dry_run:
            return out
        raise SpendError(
            422,
            "invoice_rejected",
            f"{len(report['rejected'])} line(s) of the invoice were refused; nothing was stored",
            extra={"rejected": report["rejected"]},
        )
    async with get_tenant_session(tenant_id) as session:
        await locks.xact_lock(session, locks.invoice(tenant_id, provider, p0))
        existing = await _current(session, tenant_id, provider, p0, ref)
        if existing is not None and not replace:
            raise SpendError(
                409,
                "invoice_exists",
                f"invoice {existing.id} is the current {provider} invoice {ref} for {period_text(p0)}; "
                "send replace=true to supersede it",
            )
        out["superseded_id"] = str(existing.id) if existing is not None else None
        out["created"] = len(lines)
        if dry_run:
            return out
        if existing is not None:
            # The partial unique index admits one current row per reference: retire the old one first.
            existing.status = "superseded"
            await session.flush()
        invoice = SpendInvoice(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider=provider,
            period_start=p0,
            invoice_ref=ref,
            currency=ccy,
            total_amount=total,
            usage_amount=usage,
            line_count=len(lines),
            source=imports.file_format(filename, content_type),
            file_sha256=digest,
            status="current",
            imported_by=who,
            created_at=stamp,
        )
        session.add(invoice)
        await session.flush()
        for number, line in enumerate(lines, start=1):
            session.add(
                SpendInvoiceLine(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    invoice_id=invoice.id,
                    line_no=number,
                    created_at=stamp,
                    **line,
                )
            )
        if existing is not None:
            existing.superseded_by = invoice.id
        await session.flush()
        kinds: dict[str, int] = {}
        for line in lines:
            kinds[line["line_kind"]] = kinds.get(line["line_kind"], 0) + 1
        session.add(
            audit.audit_entry(
                tenant_id,
                actor_id=who,
                action="invoices.import",
                resource_type="spend_invoice",
                resource_id=str(invoice.id),
                details={
                    "provider": provider,
                    "period": period_text(p0),
                    "invoice_ref": ref,
                    "currency": ccy,
                    "line_count": len(lines),
                    "line_kinds": kinds,
                    "total_amount": total,
                    "usage_amount": usage,
                    "file_sha256": digest,
                    "superseded_id": out["superseded_id"],
                },
                now=stamp,
            )
        )
        out["invoice_id"] = str(invoice.id)
    logger.info("spend_invoice_imported", lines=len(lines), replaced=existing is not None)
    return out


async def current_lines(
    session: Any, tenant_id: uuid.UUID, *, provider: str, period_start: date
) -> tuple[list[Any], list[Any]]:
    """The lines of every current invoice of the provider's month, and those invoices."""
    from core.models.spend_invoice import SpendInvoice, SpendInvoiceLine

    invoices = (
        (
            await session.execute(
                select(SpendInvoice)
                .where(
                    SpendInvoice.tenant_id == tenant_id,
                    SpendInvoice.provider == provider,
                    SpendInvoice.period_start == period_start,
                    SpendInvoice.status == "current",
                )
                .order_by(SpendInvoice.created_at, SpendInvoice.invoice_ref)
            )
        )
        .scalars()
        .all()
    )
    if not invoices:
        return [], []
    lines = (
        (
            await session.execute(
                select(SpendInvoiceLine)
                .where(
                    SpendInvoiceLine.tenant_id == tenant_id,
                    SpendInvoiceLine.invoice_id.in_([inv.id for inv in invoices]),
                )
                .order_by(SpendInvoiceLine.invoice_id, SpendInvoiceLine.line_no)
            )
        )
        .scalars()
        .all()
    )
    return list(lines), list(invoices)


async def list_invoices(tenant_id: uuid.UUID, *, provider: str | None = None, period: str | None = None) -> dict:
    """Invoices, current and superseded, newest month first (at most 500)."""
    from core.database import get_tenant_session
    from core.models.spend_invoice import SpendInvoice

    conditions = [SpendInvoice.tenant_id == tenant_id]
    if provider:
        conditions.append(SpendInvoice.provider == vocab.norm_provider(provider))
    if period:
        conditions.append(SpendInvoice.period_start == parse_period(period)[0])
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(SpendInvoice)
                    .where(*conditions)
                    .order_by(
                        SpendInvoice.period_start.desc(),
                        SpendInvoice.provider,
                        SpendInvoice.invoice_ref,
                        SpendInvoice.created_at.desc(),
                    )
                    .limit(LIST_LIMIT)
                )
            )
            .scalars()
            .all()
        )
    return {"items": [invoice_dict(row) for row in rows]}


async def get_invoice(tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> dict[str, Any]:
    """An invoice with its lines."""
    from core.database import get_tenant_session
    from core.models.spend_invoice import SpendInvoice, SpendInvoiceLine

    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(SpendInvoice).where(SpendInvoice.tenant_id == tenant_id, SpendInvoice.id == invoice_id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            raise SpendError(404, "not_found", "no such invoice")
        lines = (
            (
                await session.execute(
                    select(SpendInvoiceLine)
                    .where(SpendInvoiceLine.tenant_id == tenant_id, SpendInvoiceLine.invoice_id == invoice_id)
                    .order_by(SpendInvoiceLine.line_no)
                )
            )
            .scalars()
            .all()
        )
        return {**invoice_dict(rows[0]), "lines": [line_dict(line) for line in lines]}
