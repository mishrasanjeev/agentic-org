# SPDX-License-Identifier: Apache-2.0
"""Content services, part 2: narrative to payload, policy-grounded responses, tone adaptation, clause assembly."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.deps import (
    ActiveHumanAdmin,
    get_active_human_admin,
    get_current_tenant,
    get_user_domains,
    require_tenant_admin,
)
from api.route_metadata import route_meta
from api.v1.content import _off, _refused, _run
from core.content import clauses, responding, services, structuring, tone

router = APIRouter(prefix="/content", tags=["Content"])

_SERVICES = (structuring.SERVICE, responding.SERVICE, tone.SERVICE)  # imported so they register


class ClauseIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=2, max_length=80)
    title: str = Field("", max_length=200)
    category: str = Field("terms", max_length=24)
    document_types: list[str] = Field(..., min_length=1, max_length=20)
    order: int = 0
    required: bool = False
    conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    text: str = Field(..., min_length=1, max_length=20_000)


class ClausePatch(BaseModel):
    model_config = {"extra": "forbid"}

    title: str | None = Field(None, max_length=200)
    category: str | None = Field(None, max_length=24)
    document_types: list[str] | None = Field(None, min_length=1, max_length=20)
    order: int | None = None
    required: bool | None = None
    conditions: list[dict[str, Any]] | None = Field(None, max_length=20)
    text: str | None = Field(None, min_length=1, max_length=20_000)


class AssembleIn(BaseModel):
    model_config = {"extra": "forbid"}

    document_type: str = Field(..., min_length=1, max_length=64)
    facts: dict[str, Any] = Field(default_factory=dict)


@router.post("/structure")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.structure.sensitive.write",
    rate_limit="chat-query",
    idempotency="idempotent-pure",
    audit_event="content.structure",
)
async def post_structure(
    body: structuring.StructureIn,
    tenant_id: str = Depends(get_current_tenant),
    domains: list[str] | None = Depends(get_user_domains),
) -> dict[str, Any]:
    """A schema-shaped payload from free text, validated, as JSON and on request as XML."""
    return (await _run(structuring.SERVICE, body, tenant_id, domains)).to_dict()


@router.post("/respond")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.respond.sensitive.write",
    rate_limit="chat-query",
    idempotency="idempotent-pure",
    audit_event="content.respond",
)
async def post_respond(
    body: responding.RespondIn,
    tenant_id: str = Depends(get_current_tenant),
    domains: list[str] | None = Depends(get_user_domains),
) -> dict[str, Any]:
    """An answer from an approved source set with a cited quote per claim, or an honest no."""
    return (await _run(responding.SERVICE, body, tenant_id, domains)).to_dict()


@router.post("/adapt")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.adapt.sensitive.write",
    rate_limit="chat-query",
    idempotency="idempotent-pure",
    audit_event="content.adapt",
)
async def post_adapt(
    body: tone.AdaptIn,
    tenant_id: str = Depends(get_current_tenant),
    domains: list[str] | None = Depends(get_user_domains),
) -> dict[str, Any]:
    """The same facts rewritten for an audience, a tone and a reading level, figures checked."""
    return (await _run(tone.SERVICE, body, tenant_id, domains)).to_dict()


@router.post("/assemble")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.assemble.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-pure",
    audit_event="content.assemble",
)
async def post_assemble(body: AssembleIn, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """A document assembled from the approved clauses whose conditions hold, with its gaps named."""
    if not services.enabled():
        raise _off()
    return await clauses.assemble_document(uuid.UUID(tenant_id), body.document_type.strip().lower(), body.facts)


@router.get("/clauses")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.clauses.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="content.clauses.list",
)
async def list_clauses(
    document_type: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The clause library, by document type and status."""
    if not services.enabled():
        raise _off()
    rows = await clauses.list_clauses(uuid.UUID(tenant_id), document_type=document_type, status=status)
    return {"clauses": rows, "total": len(rows), "categories": list(clauses.CATEGORIES), "ops": list(clauses.OPS)}


@router.post("/clauses", dependencies=[require_tenant_admin], status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.clauses.sensitive.write",
    rate_limit="standard",
    idempotency="not-idempotent-create",
    audit_event="content.clauses.create",
)
async def create_clause(
    body: ClauseIn,
    admin: ActiveHumanAdmin = Depends(get_active_human_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict:
    """A new clause, as a draft, waiting for a second person's approval."""
    if not services.enabled():
        raise _off()
    try:
        fields = clauses.parse_clause_fields(body.model_dump())
        return await clauses.create_clause(uuid.UUID(tenant_id), fields, user_id=str(admin.user_id))
    except services.ContentError as exc:
        raise _refused(exc) from None


@router.put("/clauses/{clause_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.clauses.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-partial-update",
    audit_event="content.clauses.update",
)
async def update_clause(
    clause_id: uuid.UUID,
    body: ClausePatch,
    admin: ActiveHumanAdmin = Depends(get_active_human_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """A change: a new version of the clause that waits for approval again."""
    if not services.enabled():
        raise _off()
    try:
        fields = clauses.parse_clause_fields(body.model_dump(exclude_unset=True), partial=True)
        return await clauses.update_clause(uuid.UUID(tenant_id), clause_id, fields, user_id=str(admin.user_id))
    except services.ContentError as exc:
        raise _refused(exc) from None


@router.post("/clauses/{clause_id}/approve", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.clauses.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="content.clauses.approve",
)
async def approve_clause(
    clause_id: uuid.UUID,
    admin: ActiveHumanAdmin = Depends(get_active_human_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict:
    """Approve a clause version; its author may not."""
    if not services.enabled():
        raise _off()
    try:
        return await clauses.approve_clause(uuid.UUID(tenant_id), clause_id, user_id=str(admin.user_id), approve=True)
    except services.ContentError as exc:
        raise _refused(exc) from None


@router.post("/clauses/{clause_id}/retire", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.clauses.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="content.clauses.retire",
)
async def retire_clause(
    clause_id: uuid.UUID,
    admin: ActiveHumanAdmin = Depends(get_active_human_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict:
    """Retire a clause so it no longer assembles."""
    if not services.enabled():
        raise _off()
    try:
        return await clauses.approve_clause(uuid.UUID(tenant_id), clause_id, user_id=str(admin.user_id), approve=False)
    except services.ContentError as exc:
        raise _refused(exc) from None
