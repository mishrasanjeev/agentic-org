# SPDX-License-Identifier: Apache-2.0
"""Transaction records: what the detectors and the aggregation read, taken in as batches or from a kept statement.

A record is one movement on one account: a reference that makes it
idempotent, the account, the customer where known, the counterparty,
the direction and amount, the channel (cash, transfer, upi, cheque,
card, other), the branch, when it was booked and a description. Records
come from a caller in batches (a core banking export, a switch feed) or
from a bank statement kept by document processing, whose line items
become records on the statement's account. Every read and write is
tenant scoped under row-level security.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from core.config import settings

logger = structlog.get_logger()

DIRECTIONS = ("credit", "debit")
CHANNELS = ("cash", "transfer", "upi", "cheque", "card", "atm", "other")
MAX_BATCH = 500
MAX_LIST = 5000
MAX_AMOUNT = 1_000_000_000_000
_CASH_RE = re.compile(r"\b(cash|csh|cdm|atm dep)\b", re.I)
_UPI_RE = re.compile(r"\bupi\b", re.I)
_CHEQUE_RE = re.compile(r"\b(chq|cheque|clg)\b", re.I)
_TRANSFER_RE = re.compile(r"\b(neft|rtgs|imps|tfr|transfer|ft)\b", re.I)
_CARD_RE = re.compile(r"\b(pos|card|visa|mastercard|rupay)\b", re.I)


class TxnError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return bool(getattr(settings, "transaction_intelligence_enabled", False))


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def parse_when(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        raise TxnError(422, "record_invalid", "booked_at is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
            try:
                parsed = datetime.strptime(text, pattern).replace(tzinfo=UTC)
                break
            except ValueError:
                continue
        else:
            raise TxnError(422, "record_invalid", f"booked_at {text!r} is not a date") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def channel_of(description: str, hint: str | None = None) -> str:
    """The channel named, else read from the description, else other."""
    if hint and hint.lower() in CHANNELS:
        return hint.lower()
    text = description or ""
    if _CASH_RE.search(text):
        return "cash"
    if _UPI_RE.search(text):
        return "upi"
    if _CHEQUE_RE.search(text):
        return "cheque"
    if _TRANSFER_RE.search(text):
        return "transfer"
    if _CARD_RE.search(text):
        return "card"
    return "other"


def check_record(raw: Any) -> dict[str, Any]:
    """One record as the store keeps it, or why it cannot be."""
    if not isinstance(raw, dict):
        raise TxnError(422, "record_invalid", "each record is an object")
    account = _text(raw.get("account"), 64)
    if not account:
        raise TxnError(422, "record_invalid", "account is required")
    direction = _text(raw.get("direction"), 8).lower()
    if direction not in DIRECTIONS:
        raise TxnError(422, "record_invalid", f"direction is one of {', '.join(DIRECTIONS)}")
    try:
        amount = float(raw.get("amount"))
    except (TypeError, ValueError):
        raise TxnError(422, "record_invalid", "amount is a number") from None
    if not amount > 0 or amount > MAX_AMOUNT:
        raise TxnError(422, "record_invalid", "amount is a positive number")
    booked_at = parse_when(raw.get("booked_at"))
    description = _text(raw.get("description"), 500)
    record_ref = _text(raw.get("record_ref"), 128)
    attributes = dict(raw.get("attributes") or {}) if isinstance(raw.get("attributes"), dict) else {}
    if not record_ref:
        # Two identical lines of one statement are two movements: the source and the row number tell them apart.
        seed = "|".join(
            [
                account,
                direction,
                f"{amount:.2f}",
                booked_at.isoformat(),
                description,
                _text(raw.get("source"), 64),
                str(attributes.get("row") or ""),
                str(attributes.get("reference") or ""),
            ]
        )
        record_ref = "r-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return {
        "record_ref": record_ref,
        "account": account,
        "customer_ref": _text(raw.get("customer_ref"), 64) or None,
        "counterparty": _text(raw.get("counterparty"), 64) or None,
        "counterparty_name": _text(raw.get("counterparty_name"), 200) or None,
        "direction": direction,
        "amount": round(amount, 2),
        "currency": (_text(raw.get("currency"), 3) or "INR").upper(),
        "channel": channel_of(description, _text(raw.get("channel"), 16) or None),
        "branch": _text(raw.get("branch"), 64) or None,
        "booked_at": booked_at,
        "description": description,
        "source": _text(raw.get("source"), 64) or "api",
        "attributes": attributes,
    }


def record_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "record_ref": row.record_ref,
        "account": row.account,
        "customer_ref": row.customer_ref,
        "counterparty": row.counterparty,
        "counterparty_name": row.counterparty_name,
        "direction": row.direction,
        "amount": float(row.amount),
        "currency": row.currency,
        "channel": row.channel,
        "branch": row.branch,
        "booked_at": row.booked_at.isoformat() if row.booked_at else None,
        "description": row.description,
        "source": row.source,
        "attributes": dict(row.attributes or {}),
    }


async def ingest(tenant_id: uuid.UUID, raw_records: list[Any], *, source: str | None = None) -> dict[str, Any]:
    """Keep a batch of records; one already kept under its reference is skipped, not duplicated."""
    from core.database import get_tenant_session
    from core.models.txn_record import TxnRecord

    if not isinstance(raw_records, list) or not raw_records or len(raw_records) > MAX_BATCH:
        raise TxnError(422, "batch_invalid", f"records is a list of 1 to {MAX_BATCH} records")
    checked = [check_record(item) for item in raw_records]
    if source:
        for item in checked:
            item["source"] = source[:64]
    refs = [item["record_ref"] for item in checked]
    async with get_tenant_session(tenant_id) as session:
        existing = set(
            (
                await session.execute(
                    select(TxnRecord.record_ref).where(TxnRecord.tenant_id == tenant_id, TxnRecord.record_ref.in_(refs))
                )
            )
            .scalars()
            .all()
        )
        kept = 0
        seen: set[str] = set()
        kept_items: list[dict[str, Any]] = []
        for item in checked:
            if item["record_ref"] in existing or item["record_ref"] in seen:
                continue
            seen.add(item["record_ref"])
            session.add(TxnRecord(tenant_id=tenant_id, **item))
            kept_items.append(item)
            kept += 1
        await session.flush()
    if kept_items:
        # Provenance (core/lineage): each kept record acquired from its own source; never fails the ingestion.
        from core.lineage import provenance

        by_source: dict[str, list[dict[str, Any]]] = {}
        for item in kept_items:
            by_source.setdefault(str(item.get("source") or "api")[:64], []).append(item)
        for name, group in by_source.items():
            await provenance.on_records(tenant_id, source=name, records=group)
    logger.info("txn_records_ingested", kept=kept, skipped=len(checked) - kept)
    return {
        "received": len(checked),
        "kept": kept,
        "skipped": len(checked) - kept,
        "accounts": sorted({c["account"] for c in checked}),
    }


async def list_records(
    tenant_id: uuid.UUID,
    *,
    account: str | None = None,
    customer_ref: str | None = None,
    counterparty: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.txn_record import TxnRecord

    statement = select(TxnRecord).where(TxnRecord.tenant_id == tenant_id)
    if account:
        statement = statement.where(TxnRecord.account == account[:64])
    if customer_ref:
        statement = statement.where(TxnRecord.customer_ref == customer_ref[:64])
    if counterparty:
        # A node is an account, a counterparty reference, or a counterparty known only by name (statement imports).
        needle = counterparty[:200]
        statement = statement.where(
            (TxnRecord.counterparty == needle[:64])
            | (TxnRecord.account == needle[:64])
            | (TxnRecord.counterparty_name == needle)
        )
    if since is not None:
        statement = statement.where(TxnRecord.booked_at >= since)
    if until is not None:
        statement = statement.where(TxnRecord.booked_at <= until)
    statement = statement.order_by(TxnRecord.booked_at.asc(), TxnRecord.record_ref).limit(max(1, min(limit, MAX_LIST)))
    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(statement)).scalars().all()
    return [record_dict(row) for row in rows]


async def list_by_refs(tenant_id: uuid.UUID, refs: list[str]) -> list[dict[str, Any]]:
    """The kept records with the given references, oldest first."""
    from core.database import get_tenant_session
    from core.models.txn_record import TxnRecord

    if not refs:
        return []
    statement = (
        select(TxnRecord)
        .where(TxnRecord.tenant_id == tenant_id, TxnRecord.record_ref.in_(refs[:MAX_LIST]))
        .order_by(TxnRecord.booked_at.asc(), TxnRecord.record_ref)
    )
    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(statement)).scalars().all()
    return [record_dict(row) for row in rows]


def records_from_statement(document: dict[str, Any], *, source: str) -> list[dict[str, Any]]:
    """A kept bank statement's line items as records on its account."""
    from core.idp import statements

    fields = {f.get("name"): f.get("value") for f in document.get("fields", []) if isinstance(f, dict)}
    account = _text(fields.get("account_number"), 64)
    if not account:
        raise TxnError(422, "account_unknown", "The statement has no account number to book the records on")
    holder = _text(fields.get("name") or fields.get("account_holder"), 64) or None
    out = []
    for item in statements.analyse(document)["transactions"]:
        amount = item.get("credit") or item.get("debit")
        if not amount or not item.get("date"):
            continue
        direction = "credit" if item.get("credit") else "debit"
        out.append(
            {
                "account": account,
                "customer_ref": holder,
                "direction": direction,
                "amount": float(amount),
                "booked_at": item["date"],
                "description": item.get("description") or "",
                "counterparty_name": (item.get("description") or "")[:200] or None,
                "source": source,
                "attributes": {
                    "row": item.get("row"),
                    "reference": item.get("reference"),
                    "flags": list(item.get("flags") or []),
                },
            }
        )
    return out


async def import_document(tenant_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
    """The bank statements in a kept document, booked as records."""
    from core.idp import store

    detail = await store.get_document(tenant_id, document_id)
    if detail is None:
        raise TxnError(404, "not_found", "No such document")
    statements_found = [d for d in detail.get("documents", []) if d.get("document_type") == "bank_statement"]
    if not statements_found:
        raise TxnError(422, "no_statement", "The document holds no bank statement")
    records: list[dict[str, Any]] = []
    for document in statements_found:
        records.extend(records_from_statement(document, source=f"statement:{document_id}"))
    if not records:
        raise TxnError(422, "no_transactions", "The statement has no line items to book")
    totals = {"received": 0, "kept": 0, "skipped": 0}
    accounts: set[str] = set()
    for start in range(0, len(records), MAX_BATCH):
        answer = await ingest(tenant_id, records[start : start + MAX_BATCH])
        for key in totals:
            totals[key] += int(answer[key])
        accounts.update(answer["accounts"])
    return {
        **totals,
        "accounts": sorted(accounts),
        "document_id": str(document_id),
        "statements": len(statements_found),
    }


def window_start(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=max(1, days))
