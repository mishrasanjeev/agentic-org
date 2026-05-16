"""Generic CDC webhook endpoint — receives change-data-capture events from any connector."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from core.cdc.receiver import handle_cdc_webhook, list_stored_events, replay_cdc_event

router = APIRouter()


@router.post("/webhooks/cdc/{tenant_id}/{connector}")
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="public:cdc.webhook.ingest",
    rate_limit="provider-webhook",
    idempotency="tenant-connector-provider-event-or-fingerprint",
    audit_event="cdc.webhook.received",
    public_reason="provider-hmac-signature-required",
)
async def cdc_webhook(
    tenant_id: str,
    connector: str,
    request: Request,
) -> JSONResponse:
    """Receive a CDC webhook for a specific tenant.

    The URL carries the tenant_id so cross-tenant routing is explicit
    at the transport layer (the pre-fix `/webhooks/cdc/{connector}`
    shape stored every event in a shared list — CRITICAL-03 in the
    2026-04-19 security audit). Signature verification still uses the
    per-connector secret `CDC_WEBHOOK_SECRET_<CONNECTOR>`.

    Producers must update their webhook URL to include the tenant.
    The legacy unscoped path returns 410 Gone below.
    """
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail={"status": "rejected", "reason": "invalid_json"},
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail={"status": "rejected", "reason": "invalid_payload"},
        )

    signature = request.headers.get("X-CDC-Signature", "")
    result = await handle_cdc_webhook(
        tenant_id,
        connector,
        payload,
        signature,
        raw_body=raw_body,
    )
    http_status = int(result.pop("http_status", 202))
    if result.get("status") == "rejected":
        raise HTTPException(status_code=http_status, detail=result)
    if result.get("status") == "error":
        raise HTTPException(status_code=http_status, detail=result)
    return JSONResponse(status_code=http_status, content=result)


@router.post("/webhooks/cdc/{connector}", status_code=410)
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:cdc.webhook.legacy-removed",
    rate_limit="provider-webhook",
    idempotency="not-applicable-410",
    audit_event="cdc.webhook.legacy_rejected",
    public_reason="removed-endpoint-always-410",
)
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
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="cdc.events.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="cdc.events.list",
)
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
    start = (page - 1) * page_size
    page_events, total = await list_stored_events(
        tenant_id=tenant_id,
        connector=connector,
        event_type=event_type,
        limit=page_size,
        offset=start,
    )

    return {
        "items": page_events,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.post("/cdc/events/{event_id}/replay")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="cdc.events.replay.admin",
    rate_limit="admin-mutating",
    idempotency="tenant-event-replay-idempotent",
    audit_event="cdc.events.replay",
)
async def replay_cdc_event_endpoint(
    event_id: str,
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    _admin_check=require_tenant_admin,
) -> JSONResponse:
    """Replay one failed CDC event for the caller's tenant."""
    actor = str(user.get("sub") or user.get("email") or "admin")
    result = await replay_cdc_event(tenant_id=tenant_id, event_id=event_id, actor=actor)
    http_status = int(result.pop("http_status", 202))
    if result.get("status") == "not_found":
        raise HTTPException(status_code=http_status, detail=result)
    if result.get("status") == "failed":
        raise HTTPException(status_code=http_status, detail=result)
    return JSONResponse(status_code=http_status, content=result)
