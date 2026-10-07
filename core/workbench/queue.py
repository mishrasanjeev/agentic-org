# SPDX-License-Identifier: Apache-2.0
"""The unified review queue: everything waiting for a person, in one list, with edit before approval.

Four kinds of item wait in four stores: approvals in the human-in-the-loop
queue, documents in review, content drafts pending approval and governed
cases awaiting a decision. The queue reads them under the tenant's
row-level policy, normalises each to one shape (kind, title, summary,
priority, age, the page that shows it and the actions it allows) and
orders them by priority then age. A reviewer may edit what a store lets
them edit before deciding: a draft's text fields, a document's extracted
fields (kept as corrections with the original beside), and for an approval
a note of the amendments recorded on the item. Every decision is taken by
the store's own function, so its rules (maker-checker, role hierarchy,
expiry, policy steps) apply unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from core.workbench.access import ADMIN, WORKBENCHES, approval_filter, holds, tabs_for

logger = structlog.get_logger()

# kind -> the tab source that shows it; a caller sees a kind when a held workbench shows that source.
KINDS: dict[str, str] = {"approval": "approvals", "document": "documents", "draft": "drafts", "case": "cases"}
PRIORITY_RANK = {"critical": 0, "urgent": 0, "high": 1, "normal": 2, "medium": 2, "low": 3}
MAX_ITEMS = 200
MAX_EDITS = 50
EDIT_VALUE_MAX = 20000


class QueueError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled_kinds() -> list[str]:
    """The kinds whose subsystem is on: documents need document processing, drafts the content services."""
    from core.content import services
    from core.idp import pipeline

    out = list(KINDS)
    if not pipeline.enabled():
        out.remove("document")
    if not services.enabled():
        out.remove("draft")
    return out


def kinds_for(role: str, assigned: set[str] | None = None) -> list[str]:
    """The kinds a role may see: those shown by a tab of a workbench it holds, whose subsystem is on."""
    available = enabled_kinds()
    if role == ADMIN:
        return available
    sources: set[str] = set()
    for workbench in WORKBENCHES.values():
        if holds(workbench, role, assigned or set()):
            sources |= {tab.source for tab in tabs_for(workbench, role)}
    if "queue" in sources:
        return available
    return [kind for kind, source in KINDS.items() if source in sources and kind in available]


def _age(created_at: datetime | None, now: datetime) -> int | None:
    if created_at is None:
        return None
    stamp = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    return max(0, int((now - stamp).total_seconds()))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def approval_item(row: Any, now: datetime) -> dict[str, Any]:
    context = row.context if isinstance(row.context, dict) else {}
    summary = ", ".join(
        f"{k}: {v}" for k, v in context.items() if isinstance(v, str | int | float) and not k.startswith("_")
    )[:300]
    return {
        "kind": "approval",
        "id": str(row.id),
        "title": row.title,
        "summary": summary or row.trigger_type,
        "priority": str(row.priority or "normal"),
        "status": row.status,
        "requested_by": str(row.requested_by_user_id) if row.requested_by_user_id else None,
        "assignee_role": row.assignee_role,
        "created_at": _iso(row.created_at),
        "due_at": _iso(row.expires_at),
        "age_seconds": _age(row.created_at, now),
        "path": "/dashboard/approvals",
        "actions": ["approve", "reject", "note"],
    }


def document_item(row: Any, now: datetime) -> dict[str, Any]:
    result = row.result if isinstance(row.result, dict) else {}
    types = [str(d.get("document_type")) for d in result.get("documents", []) if isinstance(d, dict)]
    reasons = [str(r) for r in (row.review_reasons or [])]
    return {
        "kind": "document",
        "id": str(row.id),
        "title": row.filename or "document",
        "summary": "; ".join(reasons)[:300] or ", ".join(types),
        "priority": "high" if any("not recognised" in r or "not read" in r for r in reasons) else "normal",
        "status": row.status,
        "requested_by": row.created_by,
        "document_types": types,
        "created_at": _iso(row.created_at),
        "due_at": None,
        "age_seconds": _age(row.created_at, now),
        "path": "/dashboard/documents",
        "actions": ["approve", "reject", "edit"],
    }


def draft_item(row: Any, now: datetime) -> dict[str, Any]:
    return {
        "kind": "draft",
        "id": str(row.id),
        "title": row.title or row.kind,
        "summary": f"{row.service} {row.kind}"[:300],
        "priority": "normal",
        "status": row.status,
        "requested_by": row.created_by,
        "created_at": _iso(row.created_at),
        "due_at": None,
        "age_seconds": _age(row.created_at, now),
        "path": "/dashboard/workbench/review_officer/drafts",
        "actions": ["approve", "reject", "edit"],
    }


def case_item(row: Any, now: datetime) -> dict[str, Any]:
    requests = row.decision_requests if isinstance(row.decision_requests, list) else []
    return {
        "kind": "case",
        "id": str(row.id),
        "title": f"Case {row.case_ref}",
        "summary": f"{row.purpose} via {row.provider}; {len(requests)} decision request(s)"[:300],
        "priority": "high",
        "status": row.state,
        "requested_by": row.created_by,
        "case_ref": row.case_ref,
        "created_at": _iso(row.created_at),
        "due_at": None,
        "age_seconds": _age(row.created_at, now),
        "path": f"/dashboard/approvals/cases/{row.case_ref}",
        "actions": ["open"],
    }


def _rank(item: dict[str, Any]) -> tuple[int, str]:
    return (PRIORITY_RANK.get(str(item.get("priority", "normal")).lower(), 2), item.get("created_at") or "")


async def _rows(session: Any, statement: Any) -> list[Any]:
    return list((await session.execute(statement)).scalars().all())


async def list_items(tenant_id: uuid.UUID, kinds: list[str], *, limit: int = 50, caller: Any = None) -> dict[str, Any]:
    """The waiting items of the given kinds, ordered by priority then age, with a count per kind.

    Approvals are those of the agents the caller may see (``access.approval_filter``); none without a caller.
    """
    from core.database import get_tenant_session

    now = datetime.now(UTC)
    wanted = [k for k in KINDS if k in kinds]
    per_kind = max(1, min(limit, MAX_ITEMS))
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    async with get_tenant_session(tenant_id) as session:
        if "approval" in wanted:
            from core.models.hitl import HITLQueue

            rows = await _rows(
                session,
                select(HITLQueue)
                .where(
                    HITLQueue.tenant_id == tenant_id,
                    HITLQueue.status == "pending",
                    HITLQueue.expires_at > now,
                    *approval_filter(tenant_id, caller),
                )
                .order_by(HITLQueue.created_at)
                .limit(per_kind),
            )
            found = [approval_item(r, now) for r in rows]
            counts["approval"] = len(found)
            items.extend(found)
        if "document" in wanted:
            from core.models.idp_document import IdpDocument

            rows = await _rows(
                session,
                select(IdpDocument)
                .where(IdpDocument.tenant_id == tenant_id, IdpDocument.status == "review")
                .order_by(IdpDocument.created_at)
                .limit(per_kind),
            )
            found = [document_item(r, now) for r in rows]
            counts["document"] = len(found)
            items.extend(found)
        if "draft" in wanted:
            from core.models.content_draft import ContentDraft

            rows = await _rows(
                session,
                select(ContentDraft)
                .where(ContentDraft.tenant_id == tenant_id, ContentDraft.status == "pending_approval")
                .order_by(ContentDraft.created_at)
                .limit(per_kind),
            )
            found = [draft_item(r, now) for r in rows]
            counts["draft"] = len(found)
            items.extend(found)
        if "case" in wanted:
            from core.cases.states import CaseState
            from core.models.governed_case import GovernedCase

            rows = await _rows(
                session,
                select(GovernedCase)
                .where(GovernedCase.tenant_id == tenant_id, GovernedCase.state == CaseState.AWAITING_DECISION.value)
                .order_by(GovernedCase.created_at)
                .limit(per_kind),
            )
            found = [case_item(r, now) for r in rows]
            counts["case"] = len(found)
            items.extend(found)
    items.sort(key=_rank)
    return {"items": items[:per_kind], "counts": counts, "kinds": wanted}


def _editable_draft(detail: dict[str, Any]) -> list[dict[str, Any]]:
    out = [{"name": "title", "value": detail.get("title") or ""}]
    for name, value in (detail.get("output") or {}).items():
        if isinstance(value, str):
            out.append({"name": name, "value": value})
    return out


def _editable_document(detail: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for document in detail.get("documents") or []:
        index = document.get("index", 0)
        for field in list(document.get("fields") or []) + list(document.get("extra_fields") or []):
            out.append(
                {
                    "name": field.get("name"),
                    "value": field.get("value") or "",
                    "document_index": index,
                    "status": field.get("status"),
                }
            )
    return out


async def get_item(tenant_id: uuid.UUID, kind: str, item_id: str, *, caller: Any = None) -> dict[str, Any]:
    """One item in full, with the fields a reviewer may edit before deciding.

    An approval is shown only when the caller may see its agent (``access.approval_filter``), as the list does.
    """
    if kind not in KINDS:
        raise QueueError(404, "kind_unknown", f"kind is one of {', '.join(KINDS)}")
    now = datetime.now(UTC)
    if kind == "draft":
        from core.content import drafts

        try:
            draft_id = uuid.UUID(item_id)
        except ValueError:
            raise QueueError(404, "not_found", "No such item") from None
        detail = await drafts.get_draft(tenant_id, draft_id)
        if detail is None:
            raise QueueError(404, "not_found", "No such item")
        return {
            "kind": kind,
            "item": detail,
            "editable": _editable_draft(detail),
            "decidable": detail["status"] == "pending_approval",
        }
    if kind == "document":
        from core.idp import store

        try:
            document_id = uuid.UUID(item_id)
        except ValueError:
            raise QueueError(404, "not_found", "No such item") from None
        detail = await store.get_document(tenant_id, document_id)
        if detail is None:
            raise QueueError(404, "not_found", "No such item")
        return {
            "kind": kind,
            "item": detail,
            "editable": _editable_document(detail),
            "decidable": detail["status"] in ("review", "processed"),
        }
    from core.database import get_tenant_session

    if kind == "approval":
        from core.approvals.agent_run_resume import public_context
        from core.models.hitl import HITLQueue

        try:
            hitl_id = uuid.UUID(item_id)
        except ValueError:
            raise QueueError(404, "not_found", "No such item") from None
        async with get_tenant_session(tenant_id) as session:
            row = (
                await session.execute(
                    select(HITLQueue).where(
                        HITLQueue.tenant_id == tenant_id,
                        HITLQueue.id == hitl_id,
                        *approval_filter(tenant_id, caller),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise QueueError(404, "not_found", "No such item")
            item = approval_item(row, now)
            item["context"] = public_context(row.context)
            item["decision_options"] = row.decision_options
            item["review_edits"] = (
                list((row.context or {}).get("review_edits") or []) if isinstance(row.context, dict) else []
            )
            decidable = row.status == "pending" and (row.expires_at is None or row.expires_at > now)
        return {"kind": kind, "item": item, "editable": [], "decidable": decidable}
    from core.cases.states import CaseState
    from core.models.governed_case import GovernedCase

    async with get_tenant_session(tenant_id) as session:
        condition = GovernedCase.case_ref == item_id
        try:
            condition = GovernedCase.id == uuid.UUID(item_id)
        except ValueError:
            pass
        row = (
            await session.execute(select(GovernedCase).where(GovernedCase.tenant_id == tenant_id, condition))
        ).scalar_one_or_none()
        if row is None:
            raise QueueError(404, "not_found", "No such item")
        item = case_item(row, now)
        item["policy_id"] = row.policy_id
        decidable = row.state == CaseState.AWAITING_DECISION.value
    return {"kind": kind, "item": item, "editable": [], "decidable": decidable}


def check_edits(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > MAX_EDITS:
        raise QueueError(422, "edits_invalid", f"edits is a list of up to {MAX_EDITS} fields")
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise QueueError(422, "edits_invalid", "each edit names a field")
        value = entry.get("value", "")
        if not isinstance(value, str) or len(value) > EDIT_VALUE_MAX:
            raise QueueError(422, "edits_invalid", f"an edit's value is text of up to {EDIT_VALUE_MAX} characters")
        index = entry.get("document_index", 0)
        if not isinstance(index, int) or index < 0 or index > 500:
            raise QueueError(422, "edits_invalid", "document_index is a small whole number")
        out.append({"name": entry["name"].strip()[:64], "value": value, "document_index": index})
    return out


async def apply_edits(
    tenant_id: uuid.UUID, kind: str, item_id: str, edits: list[dict[str, Any]], *, user_id: str
) -> dict[str, Any] | None:
    """A reviewer's amendments before the decision, kept by the store that owns the item.

    An approval's amendments go into the decision notes and are recorded on the item by
    ``record_approval_edits`` once the approvals handler has authorised and taken the decision, so a
    refused decision leaves nothing behind.
    """
    if not edits or kind == "approval":
        return None
    if kind == "draft":
        from core.content import drafts, services

        try:
            return await drafts.edit(
                tenant_id, uuid.UUID(item_id), user_id=user_id, fields={e["name"]: e["value"] for e in edits}
            )
        except services.ContentError as exc:
            raise QueueError(exc.status, exc.code, exc.message) from None
        except ValueError:
            raise QueueError(404, "not_found", "No such item") from None
    if kind == "document":
        from core.idp import store
        from core.idp.pages import DocumentError

        answer = None
        try:
            for edit in edits:
                answer = await store.correct(
                    tenant_id,
                    uuid.UUID(item_id),
                    document_index=edit["document_index"],
                    field=edit["name"],
                    value=edit["value"],
                    user_id=user_id,
                )
        except DocumentError as exc:
            raise QueueError(exc.status, exc.code, exc.message) from None
        except ValueError:
            raise QueueError(404, "not_found", "No such item") from None
        return answer
    raise QueueError(422, "edits_unsupported", "A governed case is decided on its own page; the queue does not edit it")


async def record_approval_edits(
    tenant_id: uuid.UUID, item_id: str, edits: list[dict[str, Any]], *, user_id: str
) -> dict[str, Any] | None:
    """The amendments a reviewer made with their decision, kept on the approval item (``context.review_edits``)."""
    if not edits:
        return None
    from core.database import get_tenant_session
    from core.models.hitl import HITLQueue

    try:
        hitl_id = uuid.UUID(item_id)
    except ValueError:
        raise QueueError(404, "not_found", "No such item") from None
    stamp = datetime.now(UTC).isoformat()
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(HITLQueue).where(HITLQueue.tenant_id == tenant_id, HITLQueue.id == hitl_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise QueueError(404, "not_found", "No such item")
        context = dict(row.context or {}) if isinstance(row.context, dict) else {}
        recorded = list(context.get("review_edits") or [])
        recorded.extend(
            {"name": e["name"], "value": e["value"][:2000], "by": str(user_id)[:128], "at": stamp} for e in edits
        )
        context["review_edits"] = recorded[-MAX_EDITS * 4 :]
        row.context = context
    return {"review_edits": recorded}


def edit_note(edits: list[dict[str, Any]]) -> str:
    """The amendments as one line for a decision's notes, so a resumed run sees them."""
    if not edits:
        return ""
    return "Edited before the decision: " + "; ".join(f"{e['name']}={e['value'][:80]}" for e in edits)
