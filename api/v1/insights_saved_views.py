# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights saved views (named filter sets, personal or shared) and the "what changed" strip.

``/insights/saved-views``: list, create, change and delete saved views
(core/insights/saved_views.py). ``/insights/changes``: what changed within the
caller's reach since their last visit; ``POST /insights/changes/seen`` records
the visit (core/insights/changes.py).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1.insights import _require_enabled, _tenant
from api.v1.insights_explore import _caller
from core.database import get_tenant_session
from core.insights import changes as changes_mod
from core.insights import saved_views

router = APIRouter(prefix="/insights", tags=["Insights"])


class SavedViewIn(BaseModel):
    view_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=saved_views.MAX_NAME)
    filters: dict[str, Any] = Field(default_factory=dict)
    shared: bool = False


class SavedViewPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=saved_views.MAX_NAME)
    filters: dict[str, Any] | None = None
    shared: bool | None = None


def _refused(exc: saved_views.SavedViewError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.error, "message": exc.message})


@router.get("/saved-views")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.saved_views.read",
)
async def list_saved_views(
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    view_key: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    """The caller's saved views and the shared ones, on views the caller may open (one view, or all)."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _caller(request)
    async with get_tenant_session(tid) as session:
        return {"saved_views": await saved_views.list_views(session, tid, caller, view_key)}


@router.post("/saved-views", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="conflict-on-duplicate-name",
    audit_event="insights.saved_view.create",
)
async def create_saved_view(
    body: SavedViewIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Save the current filters of a view or explorer under a name; ``shared`` shows it to the tenant."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _caller(request)
    async with get_tenant_session(tid) as session:
        try:
            return {"saved_view": await saved_views.create_view(session, tid, caller, body.model_dump())}
        except saved_views.SavedViewError as exc:
            raise _refused(exc) from None


@router.patch("/saved-views/{view_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="idempotent-partial-update",
    audit_event="insights.saved_view.update",
)
async def update_saved_view(
    view_id: uuid.UUID, body: SavedViewPatch, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Rename, refilter, share or unshare one of the caller's saved views."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _caller(request)
    async with get_tenant_session(tid) as session:
        try:
            changed = body.model_dump(exclude_unset=True)
            return {"saved_view": await saved_views.update_view(session, tid, view_id, caller, changed)}
        except saved_views.SavedViewError as exc:
            raise _refused(exc) from None


@router.delete("/saved-views/{view_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="idempotent-delete",
    audit_event="insights.saved_view.delete",
)
async def delete_saved_view(
    view_id: uuid.UUID, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Delete a saved view: the owner's own, or (administrators) a shared one."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _caller(request)
    async with get_tenant_session(tid) as session:
        try:
            await saved_views.delete_view(session, tid, view_id, caller)
        except saved_views.SavedViewError as exc:
            raise _refused(exc) from None
    return {"deleted": str(view_id)}


@router.get("/changes")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.changes.read",
)
async def insights_changes(request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """What changed within the caller's reach since their last visit (7 days on a first visit, 30 at most)."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _caller(request)
    async with get_tenant_session(tid) as session:
        return await changes_mod.changes(session, tid, caller)


@router.post("/changes/seen")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="idempotent-update",
    audit_event="insights.changes.seen",
)
async def insights_changes_seen(request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Record that the caller has looked: the next strip counts from now."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _caller(request)
    if caller.user_id is None:
        raise HTTPException(403, detail={"error": "changes_need_a_person", "message": "only a person has a last visit"})
    async with get_tenant_session(tid) as session:
        moment = await changes_mod.mark_seen(session, tid, caller.user_id)
    return {"last_seen_at": moment.isoformat()}
