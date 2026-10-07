# SPDX-License-Identifier: Apache-2.0
"""Rule-driven clause assembly: a document built from an approved clause library by deterministic rules.

A clause belongs to document types, sits in a category with an order, and
applies when its conditions hold for the facts given (every condition, AND).
Its text carries ``{placeholders}`` filled from the facts; what is missing is
listed, never invented. Only approved clauses assemble, and a clause is
approved by a second person. No model is involved.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from core.content.services import ContentError

logger = structlog.get_logger()

OPS = ("equals", "not_equals", "in", "not_in", "gte", "lte", "gt", "lt", "exists", "missing", "contains")
CATEGORIES = ("preamble", "definitions", "terms", "charges", "obligations", "rights", "closing", "signature")
MAX_CLAUSES = 200
_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_.]{0,63})\}")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,79}$")


def parse_conditions(raw: Any) -> list[dict[str, Any]]:
    """Conditions as stored: a list of {field, op, value}; refused when malformed."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > 20:
        raise ContentError(422, "conditions_invalid", "conditions is a list of at most 20 rules")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise ContentError(422, "conditions_invalid", "each condition is an object")
        field = str(item.get("field") or "").strip()
        op = str(item.get("op") or "equals").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", field):
            raise ContentError(422, "conditions_invalid", f"bad field name {field!r}")
        if op not in OPS:
            raise ContentError(422, "conditions_invalid", f"op must be one of {', '.join(OPS)}")
        value = item.get("value")
        if op in ("in", "not_in") and not isinstance(value, list):
            raise ContentError(422, "conditions_invalid", f"{op} takes a list value")
        out.append({"field": field, "op": op, "value": value})
    return out


def _lookup(facts: dict[str, Any], field: str) -> tuple[bool, Any]:
    current: Any = facts
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def holds(condition: dict[str, Any], facts: dict[str, Any]) -> bool:
    present, actual = _lookup(facts, condition["field"])
    op, expected = condition["op"], condition.get("value")
    if op == "exists":
        return present and actual not in (None, "")
    if op == "missing":
        return not present or actual in (None, "")
    if not present:
        return False
    if op == "equals":
        return str(actual).lower() == str(expected).lower()
    if op == "not_equals":
        return str(actual).lower() != str(expected).lower()
    if op == "in":
        return str(actual).lower() in {str(v).lower() for v in expected}
    if op == "not_in":
        return str(actual).lower() not in {str(v).lower() for v in expected}
    if op == "contains":
        return str(expected).lower() in str(actual).lower()
    left, right = _number(actual), _number(expected)
    if left is None or right is None:
        return False
    return {"gte": left >= right, "lte": left <= right, "gt": left > right, "lt": left < right}[op]


def applies(conditions: list[dict[str, Any]], facts: dict[str, Any]) -> bool:
    return all(holds(c, facts) for c in conditions)


def fill(text: str, facts: dict[str, Any]) -> tuple[str, list[str]]:
    """The text with ``{placeholders}`` filled from the facts; the ones with no fact are listed and left visible."""
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        present, value = _lookup(facts, match.group(1))
        if not present or value in (None, ""):
            if match.group(1) not in missing:
                missing.append(match.group(1))
            return f"[{match.group(1).upper()}]"
        return str(value)

    return _PLACEHOLDER_RE.sub(replace, text), missing


def placeholders_of(text: str) -> list[str]:
    seen: list[str] = []
    for match in _PLACEHOLDER_RE.finditer(text):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def clause_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "title": row.title,
        "category": row.category,
        "document_types": list(row.document_types or []),
        "order": int(row.order_index or 0),
        "required": bool(row.required),
        "conditions": list(row.conditions or []),
        "text": row.text,
        "placeholders": placeholders_of(row.text or ""),
        "version": int(row.version or 1),
        "status": row.status,
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def assemble(clauses: list[dict[str, Any]], facts: dict[str, Any], document_type: str) -> dict[str, Any]:
    """The document: approved clauses of the type whose conditions hold, in category and order, filled."""
    chosen = []
    skipped = []
    for clause in clauses:
        if clause.get("status") != "approved" or document_type not in (clause.get("document_types") or []):
            continue
        if applies(clause.get("conditions") or [], facts):
            chosen.append(clause)
        else:
            skipped.append({"name": clause["name"], "reason": "conditions_not_met"})
    chosen.sort(
        key=lambda c: (
            CATEGORIES.index(c["category"]) if c["category"] in CATEGORIES else 99,
            c.get("order", 0),
            c["name"],
        )
    )
    sections = []
    missing: list[str] = []
    for clause in chosen:
        text, gaps = fill(clause["text"], facts)
        sections.append(
            {
                "name": clause["name"],
                "title": clause["title"],
                "category": clause["category"],
                "version": clause["version"],
                "text": text,
            }
        )
        missing.extend(g for g in gaps if g not in missing)
    required_missing = [
        c["name"]
        for c in clauses
        if c.get("required")
        and document_type in (c.get("document_types") or [])
        and c.get("status") == "approved"
        and c not in chosen
    ]
    body = "\n\n".join(f"{s['title']}\n{s['text']}" if s["title"] else s["text"] for s in sections)
    return {
        "document_type": document_type,
        "body": body,
        "sections": sections,
        "clauses_used": [{"name": s["name"], "version": s["version"]} for s in sections],
        "skipped": skipped,
        "missing_facts": missing,
        "required_clauses_skipped": required_missing,
        "complete": not missing and not required_missing and bool(sections),
    }


# ── The library ───────────────────────────────────────────────────────────────


def parse_clause_fields(raw: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if "name" in raw or not partial:
        name = str(raw.get("name") or "").strip().lower()
        if not _NAME_RE.match(name):
            raise ContentError(
                422, "name_invalid", "name is 2 to 80 lower-case letters, digits, hyphens or underscores"
            )
        fields["name"] = name
    if "title" in raw or not partial:
        fields["title"] = str(raw.get("title") or "")[:200]
    if "category" in raw or not partial:
        category = str(raw.get("category") or "terms")
        if category not in CATEGORIES:
            raise ContentError(422, "category_invalid", f"category is one of {', '.join(CATEGORIES)}")
        fields["category"] = category
    if "document_types" in raw or not partial:
        types = raw.get("document_types") or []
        if (
            not isinstance(types, list)
            or not types
            or len(types) > 20
            or not all(isinstance(t, str) and t for t in types)
        ):
            raise ContentError(422, "document_types_invalid", "document_types is a non-empty list of names")
        fields["document_types"] = [str(t).strip().lower()[:64] for t in types]
    if "order" in raw or not partial:
        try:
            fields["order_index"] = int(raw.get("order") or 0)
        except (TypeError, ValueError):
            raise ContentError(422, "order_invalid", "order is a whole number") from None
    if "required" in raw or not partial:
        fields["required"] = bool(raw.get("required", False))
    if "conditions" in raw or not partial:
        fields["conditions"] = parse_conditions(raw.get("conditions"))
    if "text" in raw or not partial:
        text = str(raw.get("text") or "")
        if not text.strip() or len(text) > 20_000:
            raise ContentError(422, "text_invalid", "text is non-empty and at most 20000 characters")
        fields["text"] = text
    return fields


async def list_clauses(
    tenant_id: uuid.UUID, *, document_type: str | None = None, status: str | None = None
) -> list[dict]:
    from core.database import get_tenant_session
    from core.models.content_clause import ContentClause

    async with get_tenant_session(tenant_id) as session:
        query = select(ContentClause).where(ContentClause.tenant_id == tenant_id)
        if status:
            query = query.where(ContentClause.status == status)
        rows = (
            (
                await session.execute(
                    query.order_by(ContentClause.category, ContentClause.order_index, ContentClause.name).limit(
                        MAX_CLAUSES
                    )
                )
            )
            .scalars()
            .all()
        )
    out = [clause_dict(row) for row in rows]
    if document_type:
        out = [c for c in out if document_type in c["document_types"]]
    return out


async def create_clause(tenant_id: uuid.UUID, fields: dict[str, Any], *, user_id: str) -> dict[str, Any]:
    from core.database import get_tenant_session
    from core.models.content_clause import ContentClause

    async with get_tenant_session(tenant_id) as session:
        taken = (
            await session.execute(
                select(ContentClause).where(ContentClause.tenant_id == tenant_id, ContentClause.name == fields["name"])
            )
        ).scalar_one_or_none()
        if taken is not None:
            raise ContentError(409, "name_taken", f"A clause named {fields['name']!r} exists")
        row = ContentClause(
            tenant_id=tenant_id, status="draft", version=1, created_by=str(user_id)[:128] or None, **fields
        )
        session.add(row)
        await session.flush()
        return clause_dict(row)


async def update_clause(tenant_id: uuid.UUID, clause_id: uuid.UUID, fields: dict[str, Any], *, user_id: str) -> dict:
    """A change makes a new version that waits for approval again."""
    from core.database import get_tenant_session
    from core.models.content_clause import ContentClause

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(ContentClause)
                .where(ContentClause.tenant_id == tenant_id, ContentClause.id == clause_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ContentError(404, "not_found", "No such clause")
        for key, value in fields.items():
            setattr(row, key, value)
        row.version = int(row.version or 1) + 1
        row.status = "draft"
        row.approved_by = None
        row.created_by = str(user_id)[:128] or row.created_by
        row.updated_at = datetime.now(UTC)
        return clause_dict(row)


async def approve_clause(tenant_id: uuid.UUID, clause_id: uuid.UUID, *, user_id: str, approve: bool = True) -> dict:
    """Approval by a second person; the author of the version may not approve it."""
    from core.database import get_tenant_session
    from core.models.content_clause import ContentClause

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(ContentClause)
                .where(ContentClause.tenant_id == tenant_id, ContentClause.id == clause_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ContentError(404, "not_found", "No such clause")
        if approve and row.created_by and str(row.created_by) == str(user_id)[:128]:
            raise ContentError(409, "same_person", "A clause is approved by a second person, not its author")
        row.status = "approved" if approve else "retired"
        row.approved_by = str(user_id)[:128] if approve else row.approved_by
        row.updated_at = datetime.now(UTC)
        return clause_dict(row)


async def assemble_document(tenant_id: uuid.UUID, document_type: str, facts: dict[str, Any]) -> dict[str, Any]:
    clauses = await list_clauses(tenant_id, document_type=document_type, status="approved")
    result = assemble(clauses, facts, document_type)
    if not clauses:
        result["note"] = "No approved clauses for this document type."
    logger.info(
        "content_document_assembled",
        document_type=document_type,
        clauses=len(result["clauses_used"]),
        complete=result["complete"],
    )
    return result
