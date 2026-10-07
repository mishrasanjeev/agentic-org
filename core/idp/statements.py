# SPDX-License-Identifier: Apache-2.0
"""Bank statement line items from an extracted table: dated transactions with debit, credit and running balance.

Rows are read from the statement's transaction table (date, description,
debit, credit, balance in whichever order the header gives); amounts are
parsed; the running balance is checked row by row against the opening
balance and every debit and credit, so a misread amount shows up as a break
in the arithmetic rather than a wrong total. The summary names totals,
months covered, recurring credits that look like salary, and the average
balance, each from the rows and nothing else.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core.idp.reconcile import normalise_date

_AMOUNT_RE = re.compile(r"^-?\(?(?:₹|rs\.?|inr)?\s*[\d,]+(?:\.\d{1,2})?\)?\s*(?:cr|dr)?$", re.I)
_DATE_RE = re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}")
HEADER_NAMES: dict[str, tuple[str, ...]] = {
    "date": ("date", "txn date", "transaction date", "value date"),
    "description": ("description", "narration", "particulars", "details", "remarks"),
    "debit": ("debit", "withdrawal", "withdrawals", "dr", "paid out"),
    "credit": ("credit", "deposit", "deposits", "cr", "paid in"),
    "balance": ("balance", "closing balance", "running balance"),
    "reference": ("ref", "reference", "cheque no", "chq no", "utr"),
}
SALARY_RE = re.compile(r"\b(salary|sal cr|payroll|wages|stipend)\b", re.I)
BOUNCE_RE = re.compile(r"\b(return|returned|bounce|bounced|dishonou?r|insufficient funds|ecs rtn|nach rtn)\b", re.I)
TOLERANCE = 1.0  # rupee


@dataclass
class Transaction:
    row: int
    date: str | None
    description: str
    debit: float | None
    credit: float | None
    balance: float | None
    reference: str | None = None
    expected_balance: float | None = None
    consistent: bool | None = None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "date": self.date,
            "description": self.description,
            "debit": self.debit,
            "credit": self.credit,
            "balance": self.balance,
            "reference": self.reference,
            "expected_balance": self.expected_balance,
            "consistent": self.consistent,
            "flags": list(self.flags),
        }


def parse_amount(text: str) -> float | None:
    raw = str(text or "").strip()
    if not raw or not _AMOUNT_RE.match(raw):
        return None
    negative = raw.startswith("-") or raw.startswith("(") or raw.lower().endswith("dr")
    digits = re.sub(r"[^\d.]", "", raw)
    try:
        value = float(digits)
    except ValueError:
        return None
    return -value if negative and not raw.lower().endswith("cr") else value


def map_header(header: list[str]) -> dict[str, int]:
    """Which column holds which item, from the header's words."""
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        lowered = str(cell or "").strip().lower()
        for name, aliases in HEADER_NAMES.items():
            if name in mapping:
                continue
            if any(lowered == alias or lowered.startswith(alias) for alias in aliases):
                mapping[name] = index
                break
    return mapping


def _cell(row: list[str], index: int | None) -> str:
    return str(row[index]).strip() if index is not None and index < len(row) else ""


def rows_to_transactions(header: list[str], rows: list[list[str]]) -> list[Transaction]:
    mapping = map_header(header)
    if "date" not in mapping or "balance" not in mapping or not ({"debit", "credit"} & set(mapping)):
        return []
    out: list[Transaction] = []
    for number, row in enumerate(rows, start=1):
        raw_date = _cell(row, mapping.get("date"))
        match = _DATE_RE.search(raw_date)
        when = normalise_date(match.group(0)) if match else None
        description = _cell(row, mapping.get("description"))
        debit = parse_amount(_cell(row, mapping.get("debit")))
        credit = parse_amount(_cell(row, mapping.get("credit")))
        balance = parse_amount(_cell(row, mapping.get("balance")))
        if when is None and debit is None and credit is None and balance is None:
            if out and description:
                out[-1].description = (out[-1].description + " " + description).strip()  # a continuation line
            continue
        if when is None and out:
            out[-1].description = (out[-1].description + " " + " ".join(c for c in row if c)).strip()
            continue
        flags = []
        if BOUNCE_RE.search(description):
            flags.append("returned_or_bounced")
        if SALARY_RE.search(description) and credit:
            flags.append("salary_credit")
        out.append(
            Transaction(
                number,
                when,
                description,
                debit,
                credit,
                balance,
                _cell(row, mapping.get("reference")) or None,
                flags=flags,
            )
        )
    return out


def check_running_balance(transactions: list[Transaction], opening: float | None) -> int:
    """Each row's balance against the previous balance plus credit minus debit; the number of breaks."""
    previous = opening
    breaks = 0
    for item in transactions:
        if previous is None:
            previous = item.balance
            item.consistent = None
            continue
        expected = previous + (item.credit or 0.0) - (item.debit or 0.0)
        item.expected_balance = round(expected, 2)
        if item.balance is None:
            item.consistent = None
            previous = expected
            continue
        item.consistent = abs(item.balance - expected) <= TOLERANCE
        if not item.consistent:
            breaks += 1
            item.flags.append("balance_break")
        previous = item.balance
    return breaks


def summarise(transactions: list[Transaction], *, opening: float | None, closing: float | None) -> dict[str, Any]:
    debit_total = sum(t.debit or 0.0 for t in transactions)
    credit_total = sum(t.credit or 0.0 for t in transactions)
    months = Counter(t.date[:7] for t in transactions if t.date)
    balances = [t.balance for t in transactions if t.balance is not None]
    salary = [t for t in transactions if "salary_credit" in t.flags]
    bounced = [t for t in transactions if "returned_or_bounced" in t.flags]
    first = min((t.date for t in transactions if t.date), default=None)
    last = max((t.date for t in transactions if t.date), default=None)
    span_days = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1 if first and last else None
    closing_from_rows = balances[-1] if balances else None
    return {
        "transactions": len(transactions),
        "total_debits": round(debit_total, 2),
        "total_credits": round(credit_total, 2),
        "opening_balance": opening,
        "closing_balance": closing,
        "closing_balance_from_rows": closing_from_rows,
        "closing_matches": (
            closing is not None and closing_from_rows is not None and abs(closing - closing_from_rows) <= TOLERANCE
        )
        if closing is not None and closing_from_rows is not None
        else None,
        "first_date": first,
        "last_date": last,
        "span_days": span_days,
        "months": dict(sorted(months.items())),
        "average_balance": round(sum(balances) / len(balances), 2) if balances else None,
        "minimum_balance": min(balances) if balances else None,
        "salary_credits": [{"date": t.date, "amount": t.credit, "description": t.description} for t in salary],
        "returned_or_bounced": len(bounced),
    }


def analyse(document: dict[str, Any]) -> dict[str, Any]:
    """The line items of a kept bank statement document (from the pipeline's detail), checked and summarised."""
    fields = {f.get("name"): f.get("value") for f in document.get("fields", [])}
    opening = parse_amount(str(fields.get("opening_balance") or ""))
    closing = parse_amount(str(fields.get("closing_balance") or ""))
    transactions: list[Transaction] = []
    tables_used = []
    for index, table in enumerate(document.get("tables", [])):
        parsed = rows_to_transactions(list(table.get("header", [])), [list(r) for r in table.get("rows", [])])
        if parsed:
            for item in parsed:
                item.row = len(transactions) + item.row
            transactions.extend(parsed)
            tables_used.append(index)
    breaks = check_running_balance(transactions, opening)
    summary = summarise(transactions, opening=opening, closing=closing)
    summary["balance_breaks"] = breaks
    summary["consistent"] = breaks == 0 and (summary["closing_matches"] in (True, None))
    return {
        "document_index": document.get("index"),
        "document_type": document.get("document_type"),
        "tables_used": tables_used,
        "transactions": [t.to_dict() for t in transactions],
        "summary": summary,
    }
