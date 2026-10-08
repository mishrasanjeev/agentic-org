# SPDX-License-Identifier: Apache-2.0
"""Cross-document reconciliation: fields that should agree across the documents of one file, compared.

A name on the identity document, the salary slip, the application form and
the address proof should be the same person; a date of birth on the identity
document and the KYC form should agree; a PAN on the identity document and
the tax return should match. Each disagreement names every value with its
document, page and box, so a reviewer can look. Pure and deterministic.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from itertools import combinations
from typing import Any

# Which field of which document type feeds each reconciled item.
GROUPS: dict[str, dict[str, str]] = {
    "name": {
        "government_id": "name",
        "salary_slip": "employee_name",
        "loan_application": "applicant_name",
        "address_proof": "name",
        "kyc_form": "customer_name",
        "bank_statement": "account_holder",
    },
    "date_of_birth": {"government_id": "date_of_birth", "kyc_form": "date_of_birth"},
    "pan": {"government_id": "id_number", "tax_return": "pan"},
    "employer": {"salary_slip": "employer"},
    "net_pay": {"salary_slip": "net_pay"},
    "loan_amount": {"loan_application": "loan_amount"},
}
KINDS: dict[str, str] = {
    "name": "name",
    "date_of_birth": "date",
    "pan": "id",
    "employer": "name",
    "net_pay": "amount",
    "loan_amount": "amount",
}
SEVERITY: dict[str, str] = {
    "name": "high",
    "date_of_birth": "high",
    "pan": "high",
    "employer": "medium",
    "net_pay": "medium",
    "loan_amount": "low",
}
AMOUNT_TOLERANCE = 0.02
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "kumari", "sri"}
_DATE_RES = (
    (re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$"), ("d", "m", "y")),
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), ("y", "m", "d")),
    (re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2})$"), ("d", "m", "yy")),
)
_AMOUNT_RE = re.compile(r"^\s*([+-]?)\s*(\d+(?:\.\d+)?)\s*$")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def normalise_name(value: str) -> tuple[str, ...]:
    """Case-folded, accent-free words without honorifics and punctuation, sorted so word order does not matter."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    words = [w for w in re.split(r"[^a-z0-9]+", text) if w and w not in _HONORIFICS]
    return tuple(sorted(words))


def names_agree(a: str, b: str) -> bool:
    """Equal word sets, or one side's words all in the other (an initial or a middle name left out)."""
    left, right = normalise_name(a), normalise_name(b)
    if not left or not right:
        return False
    if left == right:
        return True
    small, big = (left, right) if len(left) <= len(right) else (right, left)
    expanded = set(big) | {w[0] for w in big}
    return all(w in expanded or (len(w) == 1 and any(x.startswith(w) for x in big)) for w in small)


def _birth_century(two_digit: int, month: int, day: int, today: date) -> int:
    """The full year of a two-digit year on a date of birth: the latest century that keeps the date not in the future.

    ``01/01/90`` is 1990 and ``01/01/05`` is 2005; a two-digit year above the
    current one, or equal to it with a later day, is in the 1900s. The only
    reconciled date is a date of birth, which can never lie ahead of today.
    """
    year = (today.year // 100) * 100 + two_digit
    if (year, month, day) > (today.year, today.month, today.day):
        year -= 100
    return year


def normalise_date(value: str, *, today: date | None = None) -> str | None:
    """An ISO date, or None when the value is not a date. Two-digit years resolve as dates of birth (see above)."""
    text = str(value or "").strip()
    for pattern, order in _DATE_RES:
        match = pattern.match(text)
        if match:
            parts = dict(zip(order, match.groups(), strict=False))
            month, day = int(parts["m"]), int(parts["d"])
            if parts.get("yy") is not None:
                year = _birth_century(int(parts["yy"]), month, day, today or datetime.now(UTC).date())
            else:
                year = int(parts["y"])
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return None
    match = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})$", text)
    if match and match.group(2).lower()[:3] in _MONTHS:
        try:
            return date(
                int(match.group(3)), _MONTHS.index(match.group(2).lower()[:3]) + 1, int(match.group(1))
            ).isoformat()
        except ValueError:
            return None
    return None


def normalise_id(value: str) -> str:
    return re.sub(r"[\s-]", "", str(value or "")).upper()


def normalise_amount(value: str) -> float | None:
    """The signed number in an amount, without currency marks, separators or a trailing ``/-``; None if unclear.

    A leading minus (ASCII or the Unicode minus sign), before or after the
    currency mark, is kept, so ``-1000`` and ``1000`` never compare equal.
    """
    text = str(value or "").replace(",", "").replace("\u2212", "-").strip()
    text = re.sub(r"/-$", "", text)
    text = re.sub(r"(?i)\u20b9|inr|rs\.?", " ", text)
    match = _AMOUNT_RE.match(text)
    if not match:
        return None
    number = float(match.group(2))
    return -number if match.group(1) == "-" else number


def agree(kind: str, a: str, b: str) -> bool:
    if kind == "name":
        return names_agree(a, b)
    if kind == "date":
        left, right = normalise_date(a), normalise_date(b)
        return left is not None and left == right
    if kind == "id":
        return bool(normalise_id(a)) and normalise_id(a) == normalise_id(b)
    if kind == "amount":
        left, right = normalise_amount(a), normalise_amount(b)
        if left is None or right is None:
            return False
        return abs(left - right) <= AMOUNT_TOLERANCE * max(abs(left), abs(right), 1.0)
    return str(a).strip().casefold() == str(b).strip().casefold()


@dataclass
class Observation:
    item: str
    document_index: int
    document_type: str
    field: str
    value: str
    page: int | None = None
    bbox: list[float] | None = None
    corrected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_index": self.document_index,
            "document_type": self.document_type,
            "field": self.field,
            "value": self.value,
            "page": self.page,
            "bbox": self.bbox,
            "corrected": self.corrected,
        }


@dataclass
class Outcome:
    item: str
    kind: str
    severity: str
    status: str  # agree | disagree | single | absent
    observations: list[Observation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "kind": self.kind,
            "severity": self.severity,
            "status": self.status,
            "sources": len(self.observations),
            "values": [o.to_dict() for o in self.observations],
        }


def observations(documents: list[dict[str, Any]]) -> dict[str, list[Observation]]:
    """Every reconciled item's values across the documents, with where each came from."""
    out: dict[str, list[Observation]] = {item: [] for item in GROUPS}
    for document in documents:
        kind = str(document.get("document_type") or "")
        by_name = {f.get("name"): f for f in document.get("fields", []) if f.get("value") not in (None, "")}
        for item, sources in GROUPS.items():
            name = sources.get(kind)
            if name and name in by_name:
                found = by_name[name]
                out[item].append(
                    Observation(
                        item=item,
                        document_index=int(document.get("index", 0)),
                        document_type=kind,
                        field=name,
                        value=str(found.get("value")),
                        page=found.get("page"),
                        bbox=found.get("bbox"),
                        corrected=bool(found.get("corrected")),
                    )
                )
    return out


def reconcile(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Agreements, disagreements, items seen once and items absent, across the documents of one file."""
    outcomes: list[Outcome] = []
    for item, seen in observations(documents).items():
        kind = KINDS[item]
        if not seen:
            outcomes.append(Outcome(item, kind, SEVERITY[item], "absent"))
        elif len(seen) == 1:
            outcomes.append(Outcome(item, kind, SEVERITY[item], "single", seen))
        else:
            # Every pair must agree: names_agree accepts initials and left-out words, so it is not transitive
            # ("A Example" matches both "Anil Example" and "Arun Example", which do not match each other).
            status = "agree" if all(agree(kind, a.value, b.value) for a, b in combinations(seen, 2)) else "disagree"
            outcomes.append(Outcome(item, kind, SEVERITY[item], status, seen))
    disagreements = [o for o in outcomes if o.status == "disagree"]
    return {
        "items": [o.to_dict() for o in outcomes],
        "agreements": [o.item for o in outcomes if o.status == "agree"],
        "disagreements": [o.to_dict() for o in disagreements],
        "unverified": [o.item for o in outcomes if o.status == "single"],
        "absent": [o.item for o in outcomes if o.status == "absent"],
        "consistent": not disagreements,
        "highest_severity": max(
            (o.severity for o in disagreements), key=lambda s: ("low", "medium", "high").index(s), default=None
        ),
    }
