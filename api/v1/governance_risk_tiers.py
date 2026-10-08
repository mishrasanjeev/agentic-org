# SPDX-License-Identifier: Apache-2.0
"""Regulatory risk tiers: the policy and every agent's compliance (core/governance/risk_tiers.py)."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.governance import risk_tiers

logger = structlog.get_logger()
router = APIRouter()


@router.get("/governance/risk-tiers", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.inventory.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="governance.risk_tiers.read",
)
async def risk_tier_overview(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The tier policy (what each tier forces) and every agent's tier, compliance and unmet requirements."""
    if not risk_tiers.enabled():
        raise HTTPException(
            404,
            detail={
                "error": "governance_risk_tiers_disabled",
                "message": "Risk tiers are off for this deployment (AGENTICORG_GOVERNANCE_RISK_TIERS_ENABLED).",
            },
        )
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        return await risk_tiers.overview(session, tid)
