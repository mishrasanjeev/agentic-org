# SPDX-License-Identifier: Apache-2.0
"""The document analysis report: what a kept file contains, what agrees, what is missing, what needs a person.

Assembled from the stored result, the reviewer's corrections, the
reconciliation and the stamp check by fixed rules; the narrative is a few
sentences built from those parts, never from a model, so the report says
only what the pipeline found.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core.idp import reconcile as reconciliation
from core.idp.classify import TYPES

KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "bank_statement": ("account_number", "account_holder", "statement_period", "closing_balance"),
    "salary_slip": ("employee_name", "employer", "pay_period", "net_pay"),
    "invoice": ("invoice_number", "invoice_date", "total", "gstin"),
    "government_id": ("id_number", "name", "date_of_birth"),
    "loan_application": ("applicant_name", "loan_amount", "tenure"),
    "address_proof": ("name", "address", "bill_date"),
    "kyc_form": ("customer_name", "customer_id", "date_of_birth"),
    "tax_return": ("pan", "assessment_year", "gross_total_income"),
}


def _title(document_type: str) -> str:
    return TYPES[document_type].title if document_type in TYPES else "Untyped document"


def document_section(document: dict[str, Any]) -> dict[str, Any]:
    fields = {f.get("name"): f for f in document.get("fields", [])}
    keys = KEY_FIELDS.get(str(document.get("document_type")), tuple(fields))
    key_values = []
    for name in keys:
        item = fields.get(name)
        if item is None:
            continue
        key_values.append(
            {
                "name": name,
                "value": item.get("value"),
                "confidence": item.get("confidence"),
                "status": item.get("status"),
                "corrected": bool(item.get("corrected")),
                "page": item.get("page"),
            }
        )
    missing = [f.get("name") for f in document.get("fields", []) if f.get("required") and f.get("status") == "missing"]
    weak = [f.get("name") for f in document.get("fields", []) if f.get("status") == "weak"]
    return {
        "index": document.get("index"),
        "document_type": document.get("document_type"),
        "title": _title(str(document.get("document_type"))),
        "confidence": document.get("confidence"),
        "pages": document.get("pages", []),
        "key_fields": key_values,
        "missing_fields": missing,
        "weak_fields": weak,
        "tables": len(document.get("tables", [])),
        "review": document.get("review", {}),
    }


def narrative(sections: list[dict[str, Any]], recon: dict[str, Any], stamps: dict[str, Any] | None, status: str) -> str:
    parts = []
    if sections:
        kinds = ", ".join(f"{s['title'].lower()} (pages {', '.join(str(p) for p in s['pages'])})" for s in sections)
        parts.append(f"The file holds {len(sections)} document{'s' if len(sections) != 1 else ''}: {kinds}.")
    else:
        parts.append("The file holds no recognised document.")
    missing = [(s["title"], name) for s in sections for name in s["missing_fields"]]
    if missing:
        parts.append("Not found: " + "; ".join(f"{name} on the {title.lower()}" for title, name in missing) + ".")
    if recon.get("disagreements"):
        items = ", ".join(d["item"].replace("_", " ") for d in recon["disagreements"])
        parts.append(f"The documents disagree on: {items}.")
    elif recon.get("agreements"):
        parts.append("The documents agree on " + ", ".join(a.replace("_", " ") for a in recon["agreements"]) + ".")
    if stamps:
        present = [s for s in stamps.get("pages", []) if s.get("status") == "present"]
        missing_stamps = [s for s in stamps.get("pages", []) if s.get("status") == "missing"]
        if present:
            parts.append(
                "Ink regions consistent with a stamp or seal were found on page"
                + ("s " if len(present) != 1 else " ")
                + ", ".join(str(s["page"]) for s in present)
                + "."
            )
        if missing_stamps:
            parts.append(
                "An expected stamp was not found on " + ", ".join(f"page {s['page']}" for s in missing_stamps) + "."
            )
    parts.append(
        {
            "review": "The file is waiting for a person.",
            "approved": "The file was approved.",
            "rejected": "The file was rejected.",
            "processed": "The file needs no review.",
        }.get(status, "")
    )
    return " ".join(p for p in parts if p)


def build(detail: dict[str, Any], *, stamps: dict[str, Any] | None = None) -> dict[str, Any]:
    """The report for a kept file (``core/idp/store.py`` detail), with reconciliation and the stamp check."""
    documents = list(detail.get("documents", []))
    sections = [document_section(d) for d in documents]
    recon = reconciliation.reconcile(documents)
    status = str(detail.get("status") or "processed")
    return {
        "document_id": detail.get("id"),
        "filename": detail.get("filename"),
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "pages": detail.get("pages"),
        "documents": sections,
        "reconciliation": recon,
        "stamps": stamps,
        "review_reasons": list(detail.get("review_reasons", [])),
        "narrative": narrative(sections, recon, stamps, status),
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Document analysis: {report.get('filename') or report.get('document_id')}", "", report["narrative"], ""]
    for section in report["documents"]:
        lines.append(f"## {section['title']} (pages {', '.join(str(p) for p in section['pages'])})")
        lines.append("")
        lines.append("| Field | Value | Confidence | Status |")
        lines.append("|---|---|---|---|")
        for item in section["key_fields"]:
            value = item["value"] if item["value"] not in (None, "") else "–"
            confidence = f"{round((item['confidence'] or 0) * 100)}%" if item["status"] != "missing" else "–"
            status = "corrected" if item["corrected"] else item["status"]
            lines.append(f"| {item['name']} | {value} | {confidence} | {status} |")
        lines.append("")
    recon = report["reconciliation"]
    lines.append("## Reconciliation")
    lines.append("")
    if recon["disagreements"]:
        for item in recon["disagreements"]:
            values = "; ".join(f"{v['document_type']} p{v['page']}: {v['value']}" for v in item["values"])
            lines.append(f"- {item['item']} ({item['severity']}): {values}")
    else:
        lines.append(
            "- No disagreements." + (f" Agreed: {', '.join(recon['agreements'])}." if recon["agreements"] else "")
        )
    if recon["unverified"]:
        lines.append(f"- Seen once only: {', '.join(recon['unverified'])}.")
    lines.append("")
    if report.get("stamps"):
        lines.append("## Stamps and seals")
        lines.append("")
        for page in report["stamps"].get("pages", []):
            lines.append(f"- Page {page['page']}: {page['status']} ({len(page.get('candidates', []))} ink region(s))")
        lines.append("")
    if report["review_reasons"]:
        lines.append("## Review reasons")
        lines.append("")
        lines.extend(f"- {reason}" for reason in report["review_reasons"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
