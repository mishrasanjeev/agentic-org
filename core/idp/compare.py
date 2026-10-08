# SPDX-License-Identifier: Apache-2.0
"""Version comparison: two kept documents of the same type, field by field, page text by page, table by table.

A resubmitted statement, a corrected form or a new agreement version is
compared with what was kept before: fields that changed with both values
and their boxes, lines of page text added or removed, and tables whose rows
differ. Everything is deterministic (``difflib``); nothing is inferred.
"""

from __future__ import annotations

import difflib
from typing import Any

from core.idp.reconcile import agree

MAX_LINE_CHANGES = 200
FIELD_KINDS: dict[str, str] = {}


def _fields(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f.get("name"): f for f in document.get("fields", []) + document.get("extra_fields", []) if f.get("name")}


def compare_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Fields changed, added and removed between two documents, with values and boxes on both sides."""
    left, right = _fields(before), _fields(after)
    changed: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    same: list[dict[str, Any]] = []
    for name in sorted(set(left) | set(right)):
        a, b = left.get(name), right.get(name)
        if a is None:
            added.append({"name": name, "after": b.get("value"), "page": b.get("page"), "bbox": b.get("bbox")})
        elif b is None:
            removed.append({"name": name, "before": a.get("value"), "page": a.get("page"), "bbox": a.get("bbox")})
        else:
            kind = str(a.get("kind") or b.get("kind") or "text")
            kind = "name" if "name" in name else {"amount": "amount", "date": "date", "id": "id"}.get(kind, "text")
            av, bv = a.get("value"), b.get("value")
            equal = (av in (None, "") and bv in (None, "")) or (
                av not in (None, "") and bv not in (None, "") and agree(kind, str(av), str(bv))
            )
            entry = {
                "name": name,
                "before": av,
                "after": bv,
                "before_page": a.get("page"),
                "after_page": b.get("page"),
                "before_bbox": a.get("bbox"),
                "after_bbox": b.get("bbox"),
                "required": bool(a.get("required") or b.get("required")),
            }
            (same if equal else changed).append(entry)
    return {"changed": changed, "added": added, "removed": removed, "unchanged": [e["name"] for e in same]}


def compare_pages(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per page: lines of text added or removed, from the stored line texts."""
    out = []
    count = max(len(before), len(after))
    for index in range(count):
        left = [line.get("text", "") for line in (before[index].get("lines", []) if index < len(before) else [])]
        right = [line.get("text", "") for line in (after[index].get("lines", []) if index < len(after) else [])]
        matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
        added, removed = [], []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("replace", "delete"):
                removed.extend(left[i1:i2])
            if tag in ("replace", "insert"):
                added.extend(right[j1:j2])
        out.append(
            {
                "page": index + 1,
                "present_before": index < len(before),
                "present_after": index < len(after),
                "similarity": round(matcher.ratio(), 3),
                "added": added[:MAX_LINE_CHANGES],
                "removed": removed[:MAX_LINE_CHANGES],
                "changed": bool(added or removed),
            }
        )
    return out


def compare_tables(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    left = before.get("tables", [])
    right = after.get("tables", [])
    out = []
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        rows_a = [" | ".join(r) for r in a.get("rows", [])] if a is not None else []
        rows_b = [" | ".join(r) for r in b.get("rows", [])] if b is not None else []
        if a is None or b is None:
            # a table present on one side only: every row it holds was added or removed
            out.append(
                {
                    "table": index,
                    "present_before": a is not None,
                    "present_after": b is not None,
                    "changed": True,
                    "rows_added": rows_b[:MAX_LINE_CHANGES],
                    "rows_removed": rows_a[:MAX_LINE_CHANGES],
                }
            )
            continue
        matcher = difflib.SequenceMatcher(a=rows_a, b=rows_b, autojunk=False)
        added, removed = [], []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("replace", "delete"):
                removed.extend(rows_a[i1:i2])
            if tag in ("replace", "insert"):
                added.extend(rows_b[j1:j2])
        out.append(
            {
                "table": index,
                "present_before": True,
                "present_after": True,
                "changed": bool(added or removed),
                "rows_added": added[:MAX_LINE_CHANGES],
                "rows_removed": removed[:MAX_LINE_CHANGES],
            }
        )
    return out


def compare(before_detail: dict[str, Any], after_detail: dict[str, Any], *, document_index: int = 0) -> dict[str, Any]:
    """The comparison of one document in each kept file (by index), with the pages they span."""
    before_doc = next((d for d in before_detail.get("documents", []) if d.get("index") == document_index), None)
    after_doc = next((d for d in after_detail.get("documents", []) if d.get("index") == document_index), None)
    if before_doc is None or after_doc is None:
        return {"comparable": False, "reason": "document_index_missing", "document_index": document_index}
    same_type = before_doc.get("document_type") == after_doc.get("document_type")
    before_pages = [p for p in before_detail.get("pages_detail", []) if p.get("number") in before_doc.get("pages", [])]
    after_pages = [p for p in after_detail.get("pages_detail", []) if p.get("number") in after_doc.get("pages", [])]
    fields = compare_fields(before_doc, after_doc)
    pages = compare_pages(before_pages, after_pages)
    tables = compare_tables(before_doc, after_doc)
    return {
        "comparable": True,
        "document_index": document_index,
        "same_type": same_type,
        "document_type": {"before": before_doc.get("document_type"), "after": after_doc.get("document_type")},
        "fields": fields,
        "pages": pages,
        "tables": tables,
        "identical": same_type
        and not fields["changed"]
        and not fields["added"]
        and not fields["removed"]
        and not any(p["changed"] for p in pages)
        and not any(t["changed"] for t in tables),
        "summary": {
            "fields_changed": len(fields["changed"]),
            "fields_added": len(fields["added"]),
            "fields_removed": len(fields["removed"]),
            "pages_changed": sum(1 for p in pages if p["changed"]),
            "tables_changed": sum(1 for t in tables if t["changed"]),
        },
    }
