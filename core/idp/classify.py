# SPDX-License-Identifier: Apache-2.0
"""Document-type classification by rules: weighted patterns per type, a confidence from the weights matched.

The catalogue is synthetic and generic: the document types a bank handles in
onboarding, lending and servicing. A page below the floor is ``unknown``,
never a guess, so the review queue gets it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MIN_CONFIDENCE = 0.5
MAX_CONFIDENCE = 0.98


@dataclass(frozen=True)
class DocumentType:
    name: str
    title: str
    category: str  # identity | address | income | financial | lending | servicing | other
    patterns: tuple[tuple[str, float], ...]
    first_page_patterns: tuple[str, ...] = ()  # what the first page of this type says
    # What the end of a document of this type says (a closing balance, a net pay, a signature block):
    # evidence that the document before a repeated heading is complete, so the heading starts another one.
    last_page_patterns: tuple[str, ...] = ()


CATALOGUE: tuple[DocumentType, ...] = (
    DocumentType(
        "government_id",
        "Government identity document",
        "identity",
        (
            (r"\b(permanent account number|income tax department)\b", 0.6),
            (r"\b(unique identification|government of india)\b", 0.5),
            (r"\b(date of birth|dob)\b", 0.3),
            (r"\b[A-Z]{5}\d{4}[A-Z]\b", 0.4),
            (r"\b\d{4}\s\d{4}\s\d{4}\b", 0.4),
            (r"\b(passport|driving licen[cs]e|voter)\b", 0.5),
        ),
        (r"\b(permanent account number|government of india|passport)\b",),
        (r"\bsignature\b",),
    ),
    DocumentType(
        "address_proof",
        "Address proof",
        "address",
        (
            (r"\b(electricity|water|gas|telephone|broadband) (bill|statement)\b", 0.6),
            (r"\b(consumer (no|number|id))\b", 0.4),
            (r"\b(billing address|service address|units consumed)\b", 0.4),
            (r"\b(rent agreement|lease deed)\b", 0.6),
        ),
        (r"\b(bill|rent agreement|lease deed)\b",),
        (r"\b(amount payable|total amount|units consumed|due date)\b",),
    ),
    DocumentType(
        "bank_statement",
        "Bank statement",
        "financial",
        (
            (r"\b(account statement|statement of account|bank statement)\b", 0.6),
            (r"\b(opening balance|closing balance)\b", 0.5),
            (r"\b(ifsc|micr)\b", 0.3),
            (r"\b(withdrawal|deposit|debit|credit)\b.*\b(balance)\b", 0.3),
            (r"\bstatement period\b", 0.4),
        ),
        (r"\b(account statement|statement of account|bank statement)\b",),
        (r"\b(closing balance|end of statement)\b",),
    ),
    DocumentType(
        "salary_slip",
        "Salary slip",
        "income",
        (
            (r"\b(salary slip|pay ?slip|payslip|salary statement)\b", 0.7),
            (r"\b(basic (pay|salary)|hra|net pay|gross (pay|salary)|provident fund|pf)\b", 0.4),
            (r"\b(employee (id|code|no))\b", 0.3),
            (r"\b(pay period|month of)\b", 0.3),
        ),
        (r"\b(salary slip|pay ?slip|payslip)\b",),
        (r"\b(net (pay|salary)|take home)\b",),
    ),
    DocumentType(
        "tax_return",
        "Income tax return",
        "income",
        (
            (r"\b(income tax return|itr[- ]?[1-7v]?|acknowledgement number)\b", 0.6),
            (r"\b(assessment year|gross total income|total tax paid)\b", 0.5),
            (r"\bform 16\b", 0.6),
        ),
        (r"\b(income tax return|form 16|itr)\b",),
        (r"\b(total tax paid|verification)\b",),
    ),
    DocumentType(
        "invoice",
        "Invoice",
        "financial",
        (
            (r"\b(tax invoice|invoice (no|number|date))\b", 0.6),
            (r"\b(gstin|hsn|sac)\b", 0.4),
            (r"\b(subtotal|grand total|amount due|total amount)\b", 0.4),
            (r"\b(bill to|ship to)\b", 0.3),
        ),
        (r"\b(tax invoice|invoice)\b",),
        (r"\b(grand total|amount due|total amount|authori[sz]ed signatory)\b",),
    ),
    DocumentType(
        "loan_application",
        "Loan application form",
        "lending",
        (
            (r"\b(loan application|application form)\b", 0.6),
            (r"\b(loan amount|tenure|emi|purpose of loan)\b", 0.4),
            (r"\b(applicant|co-applicant|guarantor)\b", 0.4),
            (r"\b(declaration|i hereby declare)\b", 0.2),
        ),
        (r"\b(loan application|application form)\b",),
        (r"\b(i hereby declare|signature of (the )?applicant)\b",),
    ),
    DocumentType(
        "cheque",
        "Cheque",
        "financial",
        (
            (r"\b(pay to|or bearer|or order)\b", 0.5),
            (r"\b(rupees)\b.*\b(only)\b", 0.4),
            (r"\b(cheque|a/c payee)\b", 0.5),
            (r"\b\d{6}\b.*\b\d{9}\b", 0.2),
        ),
        (r"\b(cheque|or bearer)\b",),
        (r"\b(or bearer|or order|rupees)\b",),
    ),
    DocumentType(
        "property_document",
        "Property document",
        "lending",
        (
            (r"\b(sale deed|title deed|conveyance|encumbrance certificate|property tax receipt)\b", 0.6),
            (r"\b(survey (no|number)|plot (no|number)|khata)\b", 0.4),
            (r"\b(sub-registrar|registration (no|number))\b", 0.4),
            (r"\b(vendor|purchaser|schedule of property)\b", 0.3),
        ),
        (r"\b(sale deed|title deed|conveyance)\b",),
        (r"\b(in witness whereof|schedule of property)\b",),
    ),
    DocumentType(
        "agreement",
        "Agreement",
        "other",
        (
            (r"\b(this agreement|agreement is made|hereinafter referred)\b", 0.6),
            (r"\b(whereas|witnesseth|in witness whereof)\b", 0.4),
            (r"\b(party of the first part|the parties)\b", 0.3),
            (r"\b(terms and conditions)\b", 0.2),
        ),
        (r"\b(this agreement|agreement is made)\b",),
        (r"\b(in witness whereof)\b",),
    ),
    DocumentType(
        "kyc_form",
        "KYC form",
        "identity",
        (
            (r"\b(know your customer|kyc (form|details|update))\b", 0.7),
            (r"\b(customer id|cif)\b", 0.3),
            (r"\b(politically exposed|pep)\b", 0.3),
            (r"\b(occupation|annual income|source of funds)\b", 0.3),
        ),
        (r"\b(know your customer|kyc form)\b",),
        (r"\b(i hereby declare|declaration|signature)\b",),
    ),
)

TYPES: dict[str, DocumentType] = {item.name: item for item in CATALOGUE}
UNKNOWN = "unknown"


@dataclass
class Classification:
    document_type: str
    confidence: float
    hits: list[str] = field(default_factory=list)
    runner_up: str | None = None
    runner_up_confidence: float = 0.0
    first_page: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_type": self.document_type,
            "confidence": round(self.confidence, 3),
            "hits": len(self.hits),
            "runner_up": self.runner_up,
            "runner_up_confidence": round(self.runner_up_confidence, 3),
            "first_page": self.first_page,
        }


def scores(text: str) -> list[tuple[DocumentType, float, list[str]]]:
    """Every type the text matches, best first, with the weights it earned."""
    out = []
    for item in CATALOGUE:
        total = 0.0
        hits = []
        for pattern, weight in item.patterns:
            if re.search(pattern, text, re.I):
                total += weight
                hits.append(pattern)
        if hits:
            out.append((item, min(MAX_CONFIDENCE, total), hits))
    out.sort(key=lambda row: (-row[1], CATALOGUE.index(row[0])))
    return out


def looks_like_first_page(text: str, document_type: str) -> bool:
    item = TYPES.get(document_type)
    if item is None:
        return False
    head = text[:600]
    return any(re.search(pattern, head, re.I) for pattern in item.first_page_patterns)


def looks_like_last_page(text: str, document_type: str) -> bool:
    """Whether the text carries what the end of a document of this type says."""
    item = TYPES.get(document_type)
    if item is None:
        return False
    return any(re.search(pattern, text, re.I) for pattern in item.last_page_patterns)


def classify(text: str, *, floor: float = MIN_CONFIDENCE) -> Classification:
    """The document type of a page or a segment, or ``unknown`` below the floor."""
    ranked = scores(text or "")
    if not ranked or ranked[0][1] < floor:
        runner = ranked[0] if ranked else None
        return Classification(
            UNKNOWN,
            ranked[0][1] if ranked else 0.0,
            [],
            runner[0].name if runner else None,
            runner[1] if runner else 0.0,
        )
    best = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    return Classification(
        best[0].name,
        best[1],
        best[2],
        runner[0].name if runner else None,
        runner[1] if runner else 0.0,
        looks_like_first_page(text, best[0].name),
    )


def catalogue() -> list[dict[str, Any]]:
    return [
        {"name": item.name, "title": item.title, "category": item.category, "patterns": len(item.patterns)}
        for item in CATALOGUE
    ]
