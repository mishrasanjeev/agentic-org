# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights Level 3 entity pages and the Ctrl/Cmd-K search (core/insights/entities.py).

``GET /insights/entities/{kind}/{entity_id}`` is one agent, workflow, connector,
company, user or cost centre for the period: header facts, metric cards scoped
to it, its timeline and links to the explorers filtered to it. A kind the
caller's role may not open is 403; an entity outside the caller's reach is 404.
``GET /insights/search?q=`` finds entities by name (and a run by its id) among
the kinds the caller may open.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1.insights import CompanyId, FromDay, ToDay, _company, _context, _require_enabled, _tenant
from api.v1.insights_explore import _caller
from core.database import get_tenant_session
from core.insights import entities

router = APIRouter(prefix="/insights", tags=["Insights"])


def _entity_caller(request: Request) -> tuple[Any, entities.EntityCaller]:
    caller = _caller(request)
    return caller, entities.EntityCaller(role=caller.role, is_admin=caller.is_admin, domains=caller.domains)


@router.get("/entities/{kind}/{entity_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.entity.read",
)
async def insights_entity(
    kind: str,
    entity_id: uuid.UUID,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    from_: FromDay = None,
    to: ToDay = None,
    company_id: CompanyId = None,
) -> dict[str, Any]:
    """One entity's page for the period (default: the last 30 local days)."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    if kind not in entities.KINDS:
        raise HTTPException(404, detail={"error": "unknown_entity_kind", "message": f"no entity kind {kind!r}"})
    caller, who = _entity_caller(request)
    if not entities.may_open(kind, who):
        raise HTTPException(403, "insights_entity_forbidden")
    company = await _company(tid, company_id)
    async with get_tenant_session(tid) as session:
        ctx = await _context(session, tid, caller, start=from_, end=to, compare="previous", company=company)
        try:
            return await entities.entity(ctx, kind, entity_id, who)
        except entities.EntityNotFoundError:
            detail = {"error": "entity_not_found", "message": f"no {kind} {entity_id}"}
            raise HTTPException(404, detail=detail) from None


@router.get("/search")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.search",
)
async def insights_search(
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    q: Annotated[str, Query(min_length=1, max_length=entities.SEARCH_MAX)] = "",
) -> dict[str, Any]:
    """Entities the caller may open whose name matches ``q`` (at least two characters), and a run by its id."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller, who = _entity_caller(request)
    async with get_tenant_session(tid) as session:
        ctx = await _context(session, tid, caller, start=None, end=None, compare="none", company=None)
        return {"query": q, "results": await entities.search(ctx, q, who)}
