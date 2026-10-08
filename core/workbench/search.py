# SPDX-License-Identifier: Apache-2.0
"""Workbench search: cases, documents, customers and accounts in one query, with facets and boolean filters.

A query is words and quoted phrases, every one of which must match (a
word with a leading minus must not), over the text of each kind: a
governed case's reference, purpose, provider, state, subject and parties;
a kept document's name, status and extracted result; a customer's name,
identifiers, industry and address; an account as the account numbers
found in kept documents. Facets narrow a kind by a field with known
values (a case's state, purpose or provider; a document's status or type;
a customer's industry, state or activity) and the response counts the
values present so the person can narrow further. Every read is tenant
scoped under row-level security, bounded per kind, and a kind is searched
only where the caller holds a workbench tab that shows it.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import structlog
from sqlalchemy import Text, cast, or_, select

from core.workbench.access import ADMIN, WORKBENCHES, holds, tabs_for

logger = structlog.get_logger()

KINDS: tuple[str, ...] = ("case", "document", "customer", "account")
FACETS: dict[str, tuple[str, ...]] = {
    "case": ("state", "purpose", "provider"),
    "document": ("status", "document_type"),
    "customer": ("industry", "state_code", "active"),
    "account": ("document_type",),
}
CUSTOMER_ROLES: tuple[str, ...] = ("admin", "cfo", "coo", "auditor")  # the companies page's route guard
MAX_TERMS = 8
MAX_PER_KIND = 200
MIN_TERM = 2
_TOKEN = re.compile(r'(-?)"([^"]+)"|(-?)(\S+)')


class SearchError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def parse_query(raw: str) -> tuple[list[str], list[str]]:
    """The terms that must match and the terms that must not, from words, quoted phrases and leading minus signs."""
    must: list[str] = []
    must_not: list[str] = []
    for match in _TOKEN.finditer(raw or ""):
        negative = bool(match.group(1) or match.group(3))
        text = (match.group(2) or match.group(4) or "").strip().strip('"')
        if len(text) < MIN_TERM:
            continue
        (must_not if negative else must).append(text[:100])
    if len(must) + len(must_not) > MAX_TERMS:
        raise SearchError(422, "query_too_long", f"A query has at most {MAX_TERMS} terms")
    return must, must_not


def kinds_for(role: str, assigned: set[str] | None = None) -> list[str]:
    """The kinds a role may search: cases and documents through its workbench tabs, customers through companies."""
    sources: set[str] = set()
    for workbench in WORKBENCHES.values():
        if holds(workbench, role, assigned or set()):
            sources |= {tab.source for tab in tabs_for(workbench, role)}
    out: list[str] = []
    if role == ADMIN or "cases" in sources:
        out.append("case")
    if role == ADMIN or "documents" in sources:
        out.extend(["document", "account"])
    if role in CUSTOMER_ROLES:
        out.append("customer")
    return [kind for kind in KINDS if kind in out]


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _term_clause(columns: list[Any], must: list[str], must_not: list[str]) -> list[Any]:
    clauses = []
    for term in must:
        clauses.append(or_(*[column.ilike(_like(term), escape="\\") for column in columns]))
    for term in must_not:
        clauses.append(~or_(*[column.ilike(_like(term), escape="\\") for column in columns]))
    return clauses


def check_filters(kind: str, raw: dict[str, Any]) -> dict[str, list[str]]:
    """The facet filters a kind accepts, each a list of accepted values."""
    out: dict[str, list[str]] = {}
    for name, values in (raw or {}).items():
        if name not in FACETS[kind]:
            continue
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or len(values) > 20:
            raise SearchError(422, "filter_invalid", f"filter {name} is a list of up to 20 values")
        cleaned = [str(v).strip()[:100] for v in values if str(v).strip()]
        if name == "active" and any(v.lower() not in ("true", "false") for v in cleaned):
            raise SearchError(422, "filter_invalid", "filter active is true or false")
        if cleaned:
            out[name] = [v.lower() for v in cleaned] if name == "active" else cleaned
    return out


def _snippet(text: str, must: list[str], *, width: int = 160) -> str:
    lowered = text.lower()
    for term in must:
        at = lowered.find(term.lower())
        if at >= 0:
            start = max(0, at - 40)
            return ("…" if start else "") + text[start : start + width] + ("…" if len(text) > start + width else "")
    return text[:width] + ("…" if len(text) > width else "")


def _count(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        if value:
            out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _json_text(row: Any, attribute: str) -> str:
    value = getattr(row, attribute, None)
    if not value:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{k}: {v}" for k, v in value.items() if isinstance(v, str | int | float))
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(" ".join(str(v) for v in item.values() if isinstance(v, str | int | float)))
            elif isinstance(item, str | int | float):
                parts.append(str(item))
        return " ".join(parts)
    return str(value)


async def _case_hits(
    session: Any, tenant_id: uuid.UUID, must: list[str], must_not: list[str], filters: dict[str, list[str]], limit: int
) -> list[dict[str, Any]]:
    from core.models.governed_case import GovernedCase

    columns = [
        GovernedCase.case_ref,
        GovernedCase.purpose,
        GovernedCase.provider,
        GovernedCase.state,
        cast(GovernedCase.subject, Text),
        cast(GovernedCase.parties, Text),
    ]
    statement = select(GovernedCase).where(GovernedCase.tenant_id == tenant_id, *_term_clause(columns, must, must_not))
    for name, values in filters.items():
        statement = statement.where(getattr(GovernedCase, name).in_(values))
    rows = (await session.execute(statement.order_by(GovernedCase.updated_at.desc()).limit(limit))).scalars().all()
    hits = []
    for row in rows:
        subject = row.subject if isinstance(row.subject, dict) else {}
        parties = [str(p.get("name")) for p in (row.parties or []) if isinstance(p, dict) and p.get("name")]
        text = " ".join(filter(None, [row.purpose, row.provider, _json_text(row, "subject"), ", ".join(parties)]))
        hits.append(
            {
                "kind": "case",
                "id": str(row.id),
                "title": f"Case {row.case_ref}",
                "subtitle": f"{row.purpose} · {row.provider} · {row.state}",
                "snippet": _snippet(text, must),
                "path": f"/dashboard/approvals/cases/{row.case_ref}",
                "facets": {"state": row.state, "purpose": row.purpose, "provider": row.provider},
                "parties": parties[:10],
                "subject_ref": str(subject.get("provider_ref") or ""),
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return hits


def _document_fields(row: Any) -> list[tuple[int, str, str, str]]:
    """(document index, document type, field name, value) for every extracted field of a kept document."""
    out = []
    result = row.result if isinstance(row.result, dict) else {}
    corrections = row.corrections if isinstance(row.corrections, dict) else {}
    for document in result.get("documents", []):
        if not isinstance(document, dict):
            continue
        index = int(document.get("index", 0) or 0)
        fixes = corrections.get(str(index)) or {}
        for field in list(document.get("fields") or []) + list(document.get("extra_fields") or []):
            if not isinstance(field, dict) or not field.get("name"):
                continue
            fixed = fixes.get(field["name"]) if isinstance(fixes, dict) else None
            value = fixed.get("value") if isinstance(fixed, dict) else field.get("value")
            if value not in (None, ""):
                out.append((index, str(document.get("document_type") or "unknown"), str(field["name"]), str(value)))
    return out


async def _document_rows(
    session: Any, tenant_id: uuid.UUID, must: list[str], must_not: list[str], filters: dict[str, list[str]], limit: int
) -> list[Any]:
    from core.models.idp_document import IdpDocument

    columns = [
        IdpDocument.filename,
        IdpDocument.status,
        cast(IdpDocument.result, Text),
        cast(IdpDocument.corrections, Text),
    ]
    statement = select(IdpDocument).where(IdpDocument.tenant_id == tenant_id, *_term_clause(columns, must, must_not))
    if "status" in filters:
        statement = statement.where(IdpDocument.status.in_(filters["status"]))
    wanted = set(filters.get("document_type") or [])
    if wanted:
        statement = statement.where(
            or_(*[IdpDocument.result.contains({"documents": [{"document_type": kind}]}) for kind in sorted(wanted)])
        )
    rows = (await session.execute(statement.order_by(IdpDocument.updated_at.desc()).limit(limit))).scalars().all()
    if wanted:
        rows = [
            r
            for r in rows
            if wanted
            & {str(d.get("document_type")) for d in (r.result or {}).get("documents", []) if isinstance(d, dict)}
        ]
    return rows


def _document_hits(rows: list[Any], must: list[str]) -> list[dict[str, Any]]:
    hits = []
    for row in rows:
        result = row.result if isinstance(row.result, dict) else {}
        types = [str(d.get("document_type")) for d in result.get("documents", []) if isinstance(d, dict)]
        fields = _document_fields(row)
        text = " ".join(f"{name}: {value}" for _, _, name, value in fields)
        hits.append(
            {
                "kind": "document",
                "id": str(row.id),
                "title": row.filename or "document",
                "subtitle": f"{', '.join(types) or 'untyped'} · {row.status}",
                "snippet": _snippet(text or row.filename or "", must),
                "path": "/dashboard/documents",
                "facets": {"status": row.status, "document_type": types},
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return hits


def _account_hits(
    rows: list[Any], must: list[str], must_not: list[str], filters: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """An account for every account number found in a kept document, with the holder's name beside where read."""
    hits = []
    wanted = set(filters.get("document_type") or [])
    for row in rows:
        fields = _document_fields(row)
        names = {
            (index, name): value
            for index, _, name, value in fields
            if name in ("name", "account_holder", "employee_name")
        }
        for index, document_type, name, value in fields:
            if name not in ("account_number", "ifsc"):
                continue
            if wanted and document_type not in wanted:
                continue
            holder = (
                names.get((index, "name"))
                or names.get((index, "account_holder"))
                or names.get((index, "employee_name"))
                or ""
            )
            text = f"{name} {value} {holder} {document_type} {row.filename or ''}"
            lowered = text.lower()
            if any(term.lower() not in lowered for term in must) or any(term.lower() in lowered for term in must_not):
                continue
            hits.append(
                {
                    "kind": "account",
                    "id": f"{row.id}:{index}:{name}",
                    "title": f"{name.replace('_', ' ')} {value}",
                    "subtitle": f"{holder or 'holder not read'} · {document_type} · {row.filename or 'document'}",
                    "snippet": _snippet(text, must),
                    "path": "/dashboard/documents",
                    "facets": {"document_type": document_type},
                    "document_id": str(row.id),
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
            )
    return hits


async def _customer_hits(
    session: Any, tenant_id: uuid.UUID, must: list[str], must_not: list[str], filters: dict[str, list[str]], limit: int
) -> list[dict[str, Any]]:
    from core.models.company import Company

    columns = [
        Company.name,
        Company.pan,
        Company.gstin,
        Company.cin,
        Company.industry,
        Company.registered_address,
        Company.signatory_name,
    ]
    statement = select(Company).where(Company.tenant_id == tenant_id, *_term_clause(columns, must, must_not))
    for name in ("industry", "state_code"):
        if name in filters:
            statement = statement.where(getattr(Company, name).in_(filters[name]))
    if "active" in filters:
        statement = statement.where(Company.is_active.in_([v.lower() == "true" for v in filters["active"]]))
    rows = (await session.execute(statement.order_by(Company.name).limit(limit))).scalars().all()
    hits = []
    for row in rows:
        text = " ".join(filter(None, [row.name, row.industry, row.registered_address, row.signatory_name]))
        hits.append(
            {
                "kind": "customer",
                "id": str(row.id),
                "title": row.name,
                "subtitle": " · ".join(
                    filter(None, [row.industry, row.state_code, "active" if row.is_active else "inactive"])
                ),
                "snippet": _snippet(text, must),
                "path": f"/dashboard/companies/{row.id}",
                "facets": {
                    "industry": row.industry or "",
                    "state_code": row.state_code or "",
                    "active": "true" if row.is_active else "false",
                },
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return hits


def facets_of(hits: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, int]]]:
    """For each kind, the values present for each of its facets, with counts, from the hits."""
    out: dict[str, dict[str, dict[str, int]]] = {}
    for kind in KINDS:
        own = [h for h in hits if h["kind"] == kind]
        if not own:
            continue
        out[kind] = {}
        for facet in FACETS[kind]:
            values: list[str] = []
            for hit in own:
                value = hit.get("facets", {}).get(facet)
                values.extend(str(v) for v in value) if isinstance(value, list) else values.append(str(value or ""))
            out[kind][facet] = _count(values)
    return out


async def search(
    tenant_id: uuid.UUID,
    *,
    q: str,
    kinds: list[str],
    filters: dict[str, Any] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """The hits of every kind asked for, newest first within a kind, with facet counts and the query as parsed."""
    from core.database import get_tenant_session

    must, must_not = parse_query(q)
    if not must and not must_not and not filters:
        raise SearchError(422, "query_empty", "Give at least one term of two characters, or a facet filter")
    wanted = [k for k in KINDS if k in kinds]
    per_kind = max(1, min(limit, MAX_PER_KIND))
    hits: list[dict[str, Any]] = []
    async with get_tenant_session(tenant_id) as session:
        if "case" in wanted:
            hits.extend(
                await _case_hits(session, tenant_id, must, must_not, check_filters("case", filters or {}), per_kind)
            )
        if "document" in wanted:
            document_rows = await _document_rows(
                session, tenant_id, must, must_not, check_filters("document", filters or {}), per_kind
            )
            hits.extend(_document_hits(document_rows, must))
        if "account" in wanted:
            # The excluded terms are judged per account below, so the rows are fetched without them.
            account_rows = await _document_rows(
                session, tenant_id, must, [], check_filters("document", filters or {}), per_kind
            )
            hits.extend(_account_hits(account_rows, must, must_not, check_filters("account", filters or {}))[:per_kind])
        if "customer" in wanted:
            hits.extend(
                await _customer_hits(
                    session, tenant_id, must, must_not, check_filters("customer", filters or {}), per_kind
                )
            )
    counts = {kind: sum(1 for h in hits if h["kind"] == kind) for kind in wanted}
    return {
        "query": {"must": must, "must_not": must_not},
        "kinds": wanted,
        "hits": hits,
        "counts": counts,
        "facets": facets_of(hits),
        "total": len(hits),
    }
