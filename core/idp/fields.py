# SPDX-License-Identifier: Apache-2.0
"""Key-value extraction per document type: a label, a value pattern, and for each field found a confidence and a box.

A field's confidence combines how well the label matched, whether the value
matched its pattern, and the OCR confidence of the words that make it up. A
required field that is missing is reported as missing, with no value. A
generic pass also picks up ``Label: value`` lines the spec does not name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.idp.pages import Line, Page, union

MAX_GENERIC = 30


@dataclass(frozen=True)
class FieldSpec:
    name: str
    labels: tuple[str, ...]  # regexes that mark the label
    value: str  # regex the value must match
    required: bool = True
    kind: str = "text"  # text | amount | date | id | number


_AMOUNT = r"(?:₹|rs\.?|inr)?\s*-?\d[\d,]*(?:\.\d{1,2})?"
_DATE = r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{4}|\d{4}-\d{2}-\d{2}"
_TEXT = r"[A-Za-z][A-Za-z .,'&/-]{1,80}"

SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "bank_statement": (
        FieldSpec("account_number", (r"account (no|number|#)", r"a/c (no|number)"), r"[Xx*]*\d{4,18}", kind="id"),
        FieldSpec(
            "account_holder",
            (r"account holder|name of (the )?account holder|customer name|name",),
            _TEXT,
            required=False,
        ),
        FieldSpec(
            "statement_period",
            (r"statement period|period|from",),
            rf"(?:{_DATE})\s*(?:to|-)\s*(?:{_DATE})",
            required=False,
            kind="date",
        ),
        FieldSpec("opening_balance", (r"opening balance",), _AMOUNT, kind="amount"),
        FieldSpec("closing_balance", (r"closing balance",), _AMOUNT, kind="amount"),
        FieldSpec("ifsc", (r"ifsc( code)?",), r"[A-Z]{4}0[A-Z0-9]{6}", required=False, kind="id"),
    ),
    "salary_slip": (
        FieldSpec("employee_name", (r"employee name|name of employee|name",), _TEXT),
        FieldSpec("employee_id", (r"employee (id|code|no|number)",), r"[A-Z0-9-]{3,20}", required=False, kind="id"),
        FieldSpec("employer", (r"employer|company|organisation|organization",), _TEXT, required=False),
        FieldSpec(
            "pay_period", (r"pay period|month|period|salary for",), r"[A-Za-z]{3,9}[ -]?\d{4}|\d{2}/\d{4}", kind="date"
        ),
        FieldSpec(
            "gross_pay", (r"gross (pay|salary|earnings)|total earnings",), _AMOUNT, required=False, kind="amount"
        ),
        FieldSpec("net_pay", (r"net (pay|salary|amount)|take home",), _AMOUNT, kind="amount"),
    ),
    "invoice": (
        FieldSpec("invoice_number", (r"invoice (no|number|#)",), r"[A-Z0-9/-]{3,30}", kind="id"),
        FieldSpec("invoice_date", (r"invoice date|date",), _DATE, kind="date"),
        FieldSpec(
            "gstin", (r"gstin|gst (no|number)",), r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]", required=False, kind="id"
        ),
        FieldSpec("total", (r"grand total|total amount|amount due|total",), _AMOUNT, kind="amount"),
        FieldSpec("vendor", (r"from|seller|vendor|supplier",), _TEXT, required=False),
    ),
    "government_id": (
        FieldSpec(
            "id_number",
            (r"(permanent account number|pan|id (no|number)|number)",),
            r"[A-Z]{5}\d{4}[A-Z]|\d{4}\s\d{4}\s\d{4}|[A-Z0-9]{6,20}",
            kind="id",
        ),
        FieldSpec("name", (r"name",), _TEXT),
        FieldSpec("date_of_birth", (r"date of birth|dob|year of birth",), rf"{_DATE}|\d{{4}}", kind="date"),
        FieldSpec("father_name", (r"father'?s? name",), _TEXT, required=False),
    ),
    "loan_application": (
        FieldSpec("applicant_name", (r"applicant('s)? name|name of applicant|name",), _TEXT),
        FieldSpec("loan_amount", (r"loan amount|amount (requested|applied)",), _AMOUNT, kind="amount"),
        FieldSpec("tenure", (r"tenure|term|period",), r"\d{1,3}\s*(months?|years?)", required=False),
        FieldSpec("purpose", (r"purpose( of loan)?",), _TEXT, required=False),
        FieldSpec("mobile", (r"mobile|phone|contact",), r"\+?\d[\d -]{8,14}", required=False, kind="id"),
    ),
    "address_proof": (
        FieldSpec(
            "consumer_id",
            (r"consumer (no|number|id)|account (no|number)",),
            r"[A-Z0-9-]{4,20}",
            required=False,
            kind="id",
        ),
        FieldSpec("name", (r"name|consumer name|customer name",), _TEXT),
        FieldSpec("address", (r"address|billing address|service address",), r"[A-Za-z0-9][A-Za-z0-9 ,./#-]{8,160}"),
        FieldSpec("bill_date", (r"bill date|date|issued on",), _DATE, required=False, kind="date"),
    ),
    "kyc_form": (
        FieldSpec("customer_name", (r"customer name|name of customer|name",), _TEXT),
        FieldSpec("customer_id", (r"customer id|cif",), r"[A-Z0-9-]{4,20}", required=False, kind="id"),
        FieldSpec("date_of_birth", (r"date of birth|dob",), _DATE, required=False, kind="date"),
        FieldSpec("occupation", (r"occupation",), _TEXT, required=False),
    ),
    "tax_return": (
        FieldSpec("pan", (r"pan|permanent account number",), r"[A-Z]{5}\d{4}[A-Z]", kind="id"),
        FieldSpec("assessment_year", (r"assessment year|a\.?y\.?",), r"\d{4}-\d{2,4}", kind="date"),
        FieldSpec("gross_total_income", (r"gross total income",), _AMOUNT, required=False, kind="amount"),
        FieldSpec("acknowledgement_number", (r"acknowledgement (no|number)",), r"\d{10,20}", required=False, kind="id"),
    ),
}

_GENERIC_RE = re.compile(r"^\s*([A-Za-z][A-Za-z /&()'-]{2,40}?)\s*[:\-]\s*(\S.{0,120})$")
_LABEL_SEP = r"\s*[:\-–]?\s*"


@dataclass
class Field:
    name: str
    value: str | None
    confidence: float
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    source_text: str | None = None
    required: bool = True
    kind: str = "text"
    status: str = "found"  # found | missing | weak

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "confidence": round(self.confidence, 3),
            "page": self.page,
            "bbox": [round(v, 1) for v in self.bbox] if self.bbox else None,
            "source_text": self.source_text,
            "required": self.required,
            "kind": self.kind,
            "status": self.status,
        }


def normalise_value(value: str, kind: str) -> str:
    value = value.strip(" :;,.-")
    if kind == "amount":
        return re.sub(r"[^\d.-]", "", value.replace(",", "")) or value
    if kind == "id":
        return re.sub(r"\s+", " ", value).strip()
    return re.sub(r"\s+", " ", value)


def _value_words(line: Line, start: int) -> list[Any]:
    """The words of a line from character offset ``start`` of its text."""
    position = 0
    out = []
    for word in line.words:
        end = position + len(word.text)
        if end > start:
            out.append(word)
        position = end + 1
    return out


def _find(spec: FieldSpec, pages: list[Page]) -> Field | None:
    best: Field | None = None
    labels = (spec.labels,) if isinstance(spec.labels, str) else spec.labels
    for page in pages:
        lines = page.lines
        for index, line in enumerate(lines):
            for label in labels:
                match = re.search(rf"\b(?:{label})\b{_LABEL_SEP}", line.text, re.I)
                if not match:
                    continue
                remainder = line.text[match.end() :]
                target_line, offset, label_score = line, match.end(), 1.0
                value_match = re.search(rf"^\s*({spec.value})", remainder, re.I)
                if not value_match and index + 1 < len(lines):
                    # The value sits on the next line (a form with the label above the box).
                    target_line, offset = lines[index + 1], 0
                    value_match = re.search(rf"^\s*({spec.value})", target_line.text, re.I)
                    label_score = 0.8
                if not value_match:
                    continue
                raw = value_match.group(1)
                words = _value_words(target_line, offset + value_match.start(1))
                ocr_conf = sum(w.confidence for w in words) / len(words) if words else target_line.confidence
                confidence = round(min(0.98, 0.45 * label_score + 0.3 + 0.25 * ocr_conf), 3)
                found = Field(
                    name=spec.name,
                    value=normalise_value(raw, spec.kind),
                    confidence=confidence,
                    page=page.number,
                    bbox=union([w.bbox for w in words]) if words else target_line.bbox,
                    source_text=target_line.text[:200],
                    required=spec.required,
                    kind=spec.kind,
                    status="found" if confidence >= 0.6 else "weak",
                )
                if best is None or found.confidence > best.confidence:
                    best = found
                break
    return best


def extract(document_type: str, pages: list[Page]) -> list[Field]:
    """The fields of a document type: found with a box and a confidence, or missing."""
    out: list[Field] = []
    for spec in SPECS.get(document_type, ()):
        found = _find(spec, pages)
        if found is not None:
            out.append(found)
        else:
            out.append(Field(spec.name, None, 0.0, required=spec.required, kind=spec.kind, status="missing"))
    return out


def generic(pages: list[Page], *, known: set[str] | None = None, limit: int = MAX_GENERIC) -> list[Field]:
    """``Label: value`` lines the spec does not name, as extra fields with a lower confidence."""
    out: list[Field] = []
    seen: set[str] = set(known or set())
    for page in pages:
        for line in page.lines:
            match = _GENERIC_RE.match(line.text)
            if not match:
                continue
            name = re.sub(r"[^a-z0-9]+", "_", match.group(1).strip().lower()).strip("_")
            if not name or name in seen or len(name) > 40:
                continue
            seen.add(name)
            words = _value_words(line, match.start(2))
            out.append(
                Field(
                    name=name,
                    value=match.group(2).strip(),
                    confidence=round(
                        min(0.9, 0.5 + 0.3 * (sum(w.confidence for w in words) / len(words) if words else 0.5)), 3
                    ),
                    page=page.number,
                    bbox=union([w.bbox for w in words]) if words else line.bbox,
                    source_text=line.text[:200],
                    required=False,
                    kind="text",
                    status="found",
                )
            )
            if len(out) >= limit:
                return out
    return out
