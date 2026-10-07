# SPDX-License-Identifier: Apache-2.0
"""Workbench search: cases, documents, customers and accounts in one query, with facets and boolean filters.

``GET /workbench/search`` takes the query (words, quoted phrases, a
leading minus to exclude), the kinds to search and facet filters as
repeated query parameters (``state=awaiting_decision``,
``document_type=bank_statement``), and returns the hits with the facet
values present and their counts. A caller searches only the kinds a held
workbench shows. Off, the route is not found.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_current_tenant, get_user_role
from api.route_metadata import route_meta
from core.workbench import access, assignments, search

router = APIRouter(prefix="/workbench/search", tags=["Workbench"])

FILTER_PARAMS = ("state", "purpose", "provider", "status", "document_type", "industry", "state_code", "active")


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


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="workbench.search.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="workbench.search",
)
async def search_workbench(
    request: Request,
    q: Annotated[str, Query(max_length=300)] = "",
    kind: Annotated[list[str] | None, Query(max_length=4)] = None,
    limit: Annotated[int, Query(ge=1, le=search.MAX_PER_KIND)] = 50,
    role: str = Depends(get_user_role),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Search the kinds the caller may see; facet filters are repeated query parameters named after the facet."""
    if not access.enabled():
        raise _off()
    user = _user_id(request)
    held = await assignments.assigned_to(uuid.UUID(tenant_id), user) if user else set()
    allowed = search.kinds_for(role, held)
    if kind:
        unknown = [k for k in kind if k not in search.KINDS]
        if unknown:
            raise HTTPException(
                422, detail={"error": "kind_unknown", "message": f"kind is one of {', '.join(search.KINDS)}"}
            )
        wanted = [k for k in kind if k in allowed]
    else:
        wanted = allowed
    filters = {name: request.query_params.getlist(name) for name in FILTER_PARAMS if request.query_params.getlist(name)}
    try:
        found = await search.search(uuid.UUID(tenant_id), q=q, kinds=wanted, filters=filters, limit=limit)
    except search.SearchError as exc:
        raise HTTPException(exc.status, detail={"error": exc.code, "message": exc.message}) from None
    return {**found, "allowed_kinds": allowed, "filters": filters}
