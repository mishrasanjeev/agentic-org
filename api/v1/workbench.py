# SPDX-License-Identifier: Apache-2.0
"""Workbenches: the role-shaped consoles a person works from, their tabs and counts, and who holds which.

``GET /workbench`` lists the caller's workbenches with the tabs their role
may see; ``GET /workbench/{name}/summary`` adds the count waiting behind
each tab. Administrators read the catalogue and assign workbenches to
users. The shell is a view over pages the backend already authorises: a
tab a role may not see is not listed, and the page behind it still checks.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_user_role, require_tenant_admin
from api.route_metadata import route_meta
from core.workbench import access, assignments, definitions

router = APIRouter(prefix="/workbench", tags=["Workbench"])


class AssignmentIn(BaseModel):
    model_config = {"extra": "forbid"}

    workbenches: list[str] = Field(default_factory=list, max_length=16)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "workbench_disabled",
            "message": "Workbenches are off for this deployment (AGENTICORG_WORKBENCH_V2_ENABLED).",
        },
    )


def _user_id(request: Request) -> str:
    claims = getattr(request.state, "claims", None) or {}
    return str(claims.get("agenticorg:user_id") or claims.get("sub") or getattr(request.state, "user_sub", "") or "")


async def _assigned(tenant_id: str, request: Request) -> set[str]:
    user = _user_id(request)
    if not user:
        return set()
    return await assignments.assigned_to(uuid.UUID(tenant_id), user)


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="workbench.list",
)
async def list_workbenches(
    request: Request, role: str = Depends(get_user_role), tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """The caller's workbenches with the tabs their role may see; ``enabled: false`` and none while off."""
    if not access.enabled():
        return {"enabled": False, "workbenches": []}
    held = await _assigned(tenant_id, request)
    return {"enabled": True, "role": role, "workbenches": access.workbenches_for(role, held)}


@router.get("/catalogue", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.catalogue.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="workbench.catalogue.read",
)
async def catalogue(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Every workbench with every tab, the roles each names and the roles that hold it by default."""
    if not access.enabled():
        raise _off()
    return {"workbenches": definitions.catalogue()}


@router.get("/assignments", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.assignments.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="workbench.assignments.list",
)
async def list_assignments(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The assignments an administrator made, by user."""
    if not access.enabled():
        raise _off()
    rows = await assignments.list_assignments(uuid.UUID(tenant_id))
    return {"assignments": rows, "total": len(rows)}


@router.put("/assignments/{user_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.assignments.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="workbench.assignments.set",
)
async def set_assignments(
    user_id: str, body: AssignmentIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Replace a user's assigned workbenches; an empty list removes them all."""
    if not access.enabled():
        raise _off()
    if not user_id.strip() or len(user_id) > 128:
        raise HTTPException(422, detail={"error": "user_invalid", "message": "user_id is 1 to 128 characters"})
    try:
        names = assignments.check_names(body.workbenches)
    except assignments.AssignmentError as exc:
        raise HTTPException(exc.status, detail={"error": exc.code, "message": exc.message}) from None
    return await assignments.set_assignments(
        uuid.UUID(tenant_id), user_id.strip(), names, assigned_by=_user_id(request) or "admin"
    )


@router.get("/{name}/summary")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="workbench.summary.read",
)
async def summary(
    name: str, request: Request, role: str = Depends(get_user_role), tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """One workbench with the caller's tabs and the number of items waiting behind each."""
    if not access.enabled():
        raise _off()
    if name not in definitions.WORKBENCHES:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such workbench"})
    held = await _assigned(tenant_id, request)
    found = await access.summary(uuid.UUID(tenant_id), name, role, held)
    if found is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such workbench"})
    return found
