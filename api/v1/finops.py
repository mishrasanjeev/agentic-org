# SPDX-License-Identifier: Apache-2.0
"""FinOps: cost attribution by use case, application, business unit, department and cost centre."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.finops import attribution

logger = structlog.get_logger()
router = APIRouter()


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "finops_attribution_disabled",
            "message": "Cost attribution is off for this deployment (AGENTICORG_FINOPS_ATTRIBUTION_ENABLED).",
        },
    )


@router.get("/finops/attribution", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="finops.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="finops.attribution.read",
)
async def cost_attribution(
    days: int = Query(30, ge=1, le=attribution.MAX_DAYS),
    group_by: str = Query("use_case", max_length=32),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The attributed cost ledger folded by one dimension over the window, with totals and the unattributed share."""
    if not attribution.enabled():
        raise _off()
    if group_by not in attribution.DIMENSIONS:
        raise HTTPException(
            422,
            detail={"error": "unknown_dimension", "message": f"group_by is one of {', '.join(attribution.DIMENSIONS)}"},
        )
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        return await attribution.summary(session, tid, days=days, group_by=group_by)
