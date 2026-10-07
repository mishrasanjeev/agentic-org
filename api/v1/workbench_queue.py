# SPDX-License-Identifier: Apache-2.0
"""The unified review queue: list, inspect, edit before approval, decide.

The queue shows a caller the kinds of item a held workbench shows and
takes every decision through the store that owns the item, so the rules
of that store apply unchanged: an approval goes through the approvals
route's own function (role hierarchy, delegation, expiry, policy steps),
a document through the review store, a draft through the content drafts
store (maker-checker, administrator scope), and a governed case is
decided on its own page.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_current_user, get_user_domains, get_user_role, has_admin_scope
from api.route_metadata import route_meta
from core.workbench import access, assignments, queue

router = APIRouter(prefix="/workbench/queue", tags=["Workbench"])


class EditIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=64)
    value: str = Field("", max_length=queue.EDIT_VALUE_MAX)
    document_index: int = Field(0, ge=0, le=500)


class DecisionIn(BaseModel):
    model_config = {"extra": "forbid"}

    decision: str = Field(..., pattern="^(approve|reject)$")
    notes: str = Field("", max_length=2000)
    edits: list[EditIn] = Field(default_factory=list, max_length=queue.MAX_EDITS)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "workbench_disabled",
            "message": "Workbenches are off for this deployment (AGENTICORG_WORKBENCH_V2_ENABLED).",
        },
    )


def _refused(exc: queue.QueueError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _user_id(request: Request) -> str:
    claims = getattr(request.state, "claims", None) or {}
    return str(claims.get("agenticorg:user_id") or claims.get("sub") or getattr(request.state, "user_sub", "") or "")


async def _kinds(tenant_id: str, request: Request, role: str) -> list[str]:
    user = _user_id(request)
    held = await assignments.assigned_to(uuid.UUID(tenant_id), user) if user else set()
    return queue.kinds_for(role, held)


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.queue.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="workbench.queue.list",
)
async def list_queue(
    request: Request,
    kind: Annotated[list[str] | None, Query(max_length=4)] = None,
    limit: Annotated[int, Query(ge=1, le=queue.MAX_ITEMS)] = 50,
    role: str = Depends(get_user_role),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Everything waiting for the caller, by priority then age; ``kind`` narrows to one or more kinds."""
    if not access.enabled():
        raise _off()
    allowed = await _kinds(tenant_id, request, role)
    if kind:
        unknown = [k for k in kind if k not in queue.KINDS]
        if unknown:
            raise HTTPException(
                422, detail={"error": "kind_unknown", "message": f"kind is one of {', '.join(queue.KINDS)}"}
            )
        wanted = [k for k in kind if k in allowed]
    else:
        wanted = allowed
    found = await queue.list_items(uuid.UUID(tenant_id), wanted, limit=limit)
    return {**found, "allowed_kinds": allowed, "total": len(found["items"])}


@router.get("/{kind}/{item_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.queue.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="workbench.queue.read",
)
async def get_item(
    kind: str,
    item_id: str,
    request: Request,
    role: str = Depends(get_user_role),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """One item with the fields the reviewer may edit before deciding."""
    if not access.enabled():
        raise _off()
    if kind not in await _kinds(tenant_id, request, role):
        raise HTTPException(404, detail={"error": "not_found", "message": "No such item"})
    try:
        return await queue.get_item(uuid.UUID(tenant_id), kind, item_id[:128])
    except queue.QueueError as exc:
        raise _refused(exc) from None


@router.post("/{kind}/{item_id}/decide")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.queue.sensitive.write",
    rate_limit="approval-decision",
    idempotency="terminal-state-conflict-prevents-duplicate-decision",
    audit_event="workbench.queue.decide",
)
async def decide(
    kind: str,
    item_id: str,
    body: DecisionIn,
    background_tasks: BackgroundTasks,
    request: Request,
    role: str = Depends(get_user_role),
    tenant_id: str = Depends(get_current_tenant),
    user_claims: dict = Depends(get_current_user),
    user_domains: list[str] | None = Depends(get_user_domains),
) -> dict[str, Any]:
    """Apply the reviewer's edits, then decide through the store that owns the item."""
    if not access.enabled():
        raise _off()
    if kind not in await _kinds(tenant_id, request, role):
        raise HTTPException(404, detail={"error": "not_found", "message": "No such item"})
    if kind == "case":
        raise HTTPException(
            422, detail={"error": "case_decided_on_its_page", "message": "A governed case is decided on its own page"}
        )
    user_id = _user_id(request)
    tenant = uuid.UUID(tenant_id)
    edits = [e.model_dump() for e in body.edits]
    try:
        queue.check_edits(edits)
    except queue.QueueError as exc:
        raise _refused(exc) from None
    if kind == "draft" and not has_admin_scope(getattr(request.state, "scopes", []) or []):
        raise HTTPException(
            403, detail={"error": "forbidden", "message": "Deciding a draft needs the administrator scope"}
        )
    try:
        edited = await queue.apply_edits(tenant, kind, item_id[:128], edits, user_id=user_id)
    except queue.QueueError as exc:
        raise _refused(exc) from None
    notes = " ".join(part for part in (body.notes.strip(), queue.edit_note(edits)) if part)[:2000]
    if kind == "approval":
        from api.v1 import approvals as approvals_api
        from core.schemas.api import HITLDecision

        try:
            hitl_id = uuid.UUID(item_id)
        except ValueError:
            raise HTTPException(404, detail={"error": "not_found", "message": "No such item"}) from None
        outcome = await approvals_api.decide(
            hitl_id,
            HITLDecision(decision=body.decision, notes=notes),
            background_tasks,
            request,
            tenant_id,
            user_claims,
            role,
            user_domains,
        )
    elif kind == "document":
        from core.idp import store
        from core.idp.pages import DocumentError

        try:
            outcome = await store.decide(
                tenant, uuid.UUID(item_id), decision=body.decision, user_id=user_id, notes=notes
            )
        except DocumentError as exc:
            raise HTTPException(exc.status, detail={"error": exc.code, "message": exc.message}) from None
        except ValueError:
            raise HTTPException(404, detail={"error": "not_found", "message": "No such item"}) from None
    else:
        from core.content import drafts, services

        try:
            outcome = await drafts.decide(
                tenant, uuid.UUID(item_id), user_id=user_id, decision=body.decision, notes=notes
            )
        except services.ContentError as exc:
            raise HTTPException(exc.status, detail={"error": exc.code, "message": exc.message}) from None
        except ValueError:
            raise HTTPException(404, detail={"error": "not_found", "message": "No such item"}) from None
    return {
        "kind": kind,
        "id": item_id[:128],
        "decision": body.decision,
        "edited": edited is not None,
        "outcome": outcome,
    }
