"""Generic CDC webhook endpoint — receives change-data-capture events from any connector."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from api.deps import get_current_tenant
from core.cdc.receiver import get_stored_events, handle_cdc_webhook

router = APIRouter()


@router.post("/webhooks/cdc/{tenant_id}/{connector}")
async def cdc_webhook(
    tenant_id: str,
    connector: str,
    request: Request,
) -> dict[str, Any]:
    """Receive a CDC webhook for a specific tenant.

    The URL carries the tenant_id so cross-tenant routing is explicit
    at the transport layer (the pre-fix `/webhooks/cdc/{connector}`
    shape stored every event in a shared list — CRITICAL-03 in the
    2026-04-19 security audit). Signature verification still uses the
    per-connector secret `CDC_WEBHOOK_SECRET_<CONNECTOR>`.

    Producers must update their webhook URL to include the tenant.
    The legacy unscoped path returns 410 Gone below.
    """
    payload: dict[str, Any] = await request.json()
    signature = request.headers.get("X-CDC-Signature", "")
    result = await handle_cdc_webhook(tenant_id, connector, payload, signature)
    return result


@router.post("/webhooks/cdc/{connector}", status_code=410)
async def cdc_webhook_legacy(connector: str) -> dict[str, Any]:
    """Legacy unscoped CDC URL — 410 Gone.

    Events posted here would land in a shared store with no tenant
    ownership tag; rejecting keeps that leak closed. Update callers
    to `/webhooks/cdc/{tenant_id}/{connector}`.
    """
    return {
        "error": "endpoint_removed",
        "message": (
            "POST /webhooks/cdc/{connector} was removed for cross-tenant "
            "isolation. Use /webhooks/cdc/{tenant_id}/{connector} "
            "(see SECURITY_AUDIT_2026-04-19.md CRITICAL-03)."
        ),
        "connector": connector,
    }


@router.get("/cdc/events")
async def list_cdc_events(
    connector: str | None = Query(None, description="Filter by connector name"),
    event_type: str | None = Query(None, description="Filter by event type"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Return stored CDC events for the CALLER's tenant only.

    Enforces tenant isolation at the read layer so a global in-memory
    store never leaks cross-tenant events (CRITICAL-03 fix).
    """
    events = get_stored_events(tenant_id=tenant_id)

    # Apply optional filters
    if connector:
        events = [e for e in events if e.get("connector") == connector]
    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]

    total = len(events)

    # Paginate (newest first)
    events = list(reversed(events))
    start = (page - 1) * page_size
    end = start + page_size
    page_events = events[start:end]

    return {
        "items": page_events,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }
