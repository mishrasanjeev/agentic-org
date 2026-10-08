# SPDX-License-Identifier: Apache-2.0
"""The AI asset inventory and its bill-of-materials export (core/governance/inventory.py)."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.governance import inventory

logger = structlog.get_logger()
router = APIRouter()


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "governance_inventory_disabled",
            "message": "The AI inventory is off for this deployment (AGENTICORG_GOVERNANCE_INVENTORY_ENABLED).",
        },
    )


async def _collect(tenant_id: str) -> inventory.Inventory:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        return await inventory.collect(session, tid)


@router.get("/governance/inventory", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.inventory.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="governance.inventory.read",
)
async def list_inventory(
    kind: str | None = Query(None, max_length=20),
    risk_tier: str | None = Query(None, max_length=10),
    q: str | None = Query(None, max_length=200),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The tenant's AI assets with owner, version, risk tier, status and dependencies; filters narrow the list."""
    if not inventory.enabled():
        raise _off()
    if kind is not None and kind not in inventory.KINDS:
        raise HTTPException(
            422, detail={"error": "unknown_kind", "message": f"kind is one of {', '.join(inventory.KINDS)}"}
        )
    if risk_tier is not None and risk_tier not in (*inventory.TIERS, "unset"):
        raise HTTPException(
            422,
            detail={
                "error": "unknown_risk_tier",
                "message": f"risk_tier is one of {', '.join(inventory.TIERS)} or unset",
            },
        )
    inv = await _collect(tenant_id)
    return inv.as_dict(kind=kind, risk_tier=risk_tier, q=q)


@router.get("/governance/inventory/export", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.inventory.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="governance.inventory.export",
)
async def export_inventory(
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The inventory as an AI bill of materials: components, dependencies and a summary, as JSON."""
    if not inventory.enabled():
        raise _off()
    inv = await _collect(tenant_id)
    return inventory.export(inv, tenant_id=uuid.UUID(tenant_id))
