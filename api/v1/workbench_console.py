# SPDX-License-Identifier: Apache-2.0
"""The business console: the rules, thresholds and routing a tenant changes without a release.

``GET /workbench/console`` lists every setting with its definition, bounds,
options, the tenant's value where one is set and the default. An
administrator sets a value (``PUT /workbench/console/{key}``) or removes
it so the default applies again (``DELETE``); each change keeps the
previous value and writes an audit row. Off, every route is not found.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.workbench import access, console

router = APIRouter(prefix="/workbench/console", tags=["Workbench"])


class ValueIn(BaseModel):
    model_config = {"extra": "forbid"}

    value: Any


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "workbench_disabled",
            "message": "Workbenches are off for this deployment (AGENTICORG_WORKBENCH_V2_ENABLED).",
        },
    )


def _refused(exc: console.ConsoleError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _user_id(request: Request) -> str:
    claims = getattr(request.state, "claims", None) or {}
    return str(claims.get("agenticorg:user_id") or claims.get("sub") or getattr(request.state, "user_sub", "") or "")


@router.get("", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.console.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="workbench.console.list",
)
async def list_settings(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Every setting by group: definition, bounds, options, the tenant's value or the default, who changed it."""
    if not access.enabled():
        raise _off()
    found = await console.values(uuid.UUID(tenant_id))
    groups = [
        {"key": key, "title": title, "settings": [item for item in found if item["group"] == key]}
        for key, title in console.GROUPS
    ]
    # A group with nothing in it (speech while speech intelligence is off) is not listed.
    groups = [group for group in groups if group["settings"]]
    return {"groups": groups, "total": len(found)}


@router.put("/{key}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.console.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="workbench.console.set",
)
async def set_setting(
    key: str, body: ValueIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Set the tenant's value for a setting, within the catalogue's bounds; the previous value is kept."""
    if not access.enabled():
        raise _off()
    try:
        return await console.put(uuid.UUID(tenant_id), key[:64], body.value, actor=_user_id(request) or "admin")
    except console.ConsoleError as exc:
        raise _refused(exc) from None


@router.delete("/{key}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.console.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="workbench.console.reset",
)
async def reset_setting(key: str, request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Remove the tenant's value so the default applies again."""
    if not access.enabled():
        raise _off()
    try:
        return await console.reset(uuid.UUID(tenant_id), key[:64], actor=_user_id(request) or "admin")
    except console.ConsoleError as exc:
        raise _refused(exc) from None
