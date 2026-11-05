# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights benchmarks (``/insights/benchmarks``): the organisation against opted-in cohorts, and opting in or out.

``GET`` answers administrators, the CFO and the COO: the organisation's own
figures over the last 30 complete days against the published cohort
percentiles (core/insights/benchmarks.py), or why there are none. ``PUT
/insights/benchmarks/opt-in`` is an active human administrator's choice and is
audited.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.deps import ActiveHumanAdmin, get_active_human_admin, get_current_tenant
from api.route_metadata import route_meta
from api.v1.insights import _require_enabled, _tenant
from core.database import get_tenant_session
from core.insights import benchmarks
from core.ownership import caller_from_request

router = APIRouter(prefix="/insights/benchmarks", tags=["Insights"])
Admin = Annotated[ActiveHumanAdmin, Depends(get_active_human_admin)]


class OptIn(BaseModel):
    opt_in: bool


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.benchmarks.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.benchmarks.read",
)
async def read_benchmarks(request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The organisation's figures against the opted-in cohorts it belongs to (all, and its plan)."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = caller_from_request(request)
    if not benchmarks.may_read(caller.role, is_admin=caller.is_admin):
        raise HTTPException(403, "insights_benchmarks_forbidden")
    async with get_tenant_session(tid) as session:
        body = await benchmarks.card(session, tid)
    return {**body, "can_change": bool(caller.is_admin and not caller.is_machine)}


@router.put("/opt-in")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights_admin.benchmarks.write",
    rate_limit="reporting-admin",
    idempotency="idempotent-upsert-by-tenant",
    audit_event="insights.benchmarks.opt_in",
)
async def set_opt_in(body: OptIn, admin: Admin, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Opt the organisation in to (or out of) cross-tenant benchmarks."""
    tid = _tenant(tenant_id)
    if tid != admin.tenant_id:
        raise HTTPException(403, "insights_benchmarks_forbidden")
    await _require_enabled(tid)
    async with get_tenant_session(tid) as session:
        return await benchmarks.set_opt_in(session, tid, admin.user_id, body.opt_in)
