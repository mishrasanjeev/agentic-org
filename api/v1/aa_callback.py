"""Account Aggregator consent callback and management endpoints."""

from __future__ import annotations

from typing import Any

import structlog
from api.deps import get_current_tenant
from fastapi import APIRouter, Depends, HTTPException

from connectors.finance.aa_consent_types import AACallbackPayload

logger = structlog.get_logger()

router = APIRouter(prefix="/aa", tags=["Account Aggregator"])

# Consent manager instances per tenant (in production, use a service registry)
_consent_managers: dict[str, Any] = {}


def _get_consent_manager(tenant_id: str):
    """Get or create consent manager for a tenant."""
    from connectors.finance.aa_consent import AAConsentManager

    if tenant_id not in _consent_managers:
        _consent_managers[tenant_id] = AAConsentManager()
    return _consent_managers[tenant_id]


@router.post("/consent/callback")
async def consent_callback(payload: AACallbackPayload) -> dict[str, str]:
    """Webhook endpoint called by Finvu when consent status changes.

    This is a public endpoint (no auth) — Finvu sends callbacks here
    after the user approves/rejects consent on their UI.
    """
    logger.info(
        "aa_consent_callback_received",
        consent_handle=payload.consent_handle,
        status=payload.consent_status.value,
        consent_id=payload.consent_id,
    )

    # Find the consent manager that created this handle
    for manager in _consent_managers.values():
        result = await manager.handle_consent_callback(
            consent_handle=payload.consent_handle,
            consent_status=payload.consent_status,
            consent_id=payload.consent_id,
        )
        if "error" not in result:
            return {"status": "ok", "consent_handle": payload.consent_handle}

    # If no manager knows this handle, still acknowledge (idempotent)
    logger.warning("aa_consent_callback_unmatched", handle=payload.consent_handle)
    return {"status": "ok", "consent_handle": payload.consent_handle}


@router.get("/consent/{consent_handle}/status")
async def consent_status(
    consent_handle: str,
    tenant: dict = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Check the status of a consent request."""
    tenant_id = tenant.get("tenant_id", "")
    manager = _consent_managers.get(tenant_id)
    if not manager:
        raise HTTPException(status_code=404, detail="No consent manager for tenant")

    result = manager.get_consent_status(consent_handle)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/consent/request")
async def create_consent_request(
    params: dict[str, Any],
    tenant: dict = Depends(get_current_tenant),
) -> dict[str, str]:
    """Create a new AA consent request.

    Returns consent_handle and redirect_url for user approval.
    """
    from connectors.finance.aa_consent_types import ConsentRequest

    tenant_id = tenant.get("tenant_id", "")
    manager = _get_consent_manager(tenant_id)

    request = ConsentRequest(**params)
    result = await manager.create_consent_request(request)
    return result
