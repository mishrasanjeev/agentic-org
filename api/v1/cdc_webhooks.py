"""Generic CDC webhook endpoint — receives change-data-capture events from any connector."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from core.cdc.receiver import get_stored_events, handle_cdc_webhook

router = APIRouter()


@router.post("/webhooks/cdc/{connector}")
async def cdc_webhook(connector: str, request: Request) -> dict[str, Any]:
    """Receive a CDC webhook from any connector.

    The connector name is passed as a path parameter.  Signature validation
    uses a per-connector secret from the environment (CDC_WEBHOOK_SECRET_<CONNECTOR>).
    """
    payload: dict[str, Any] = await request.json()
    signature = request.headers.get("X-CDC-Signature", "")
    result = await handle_cdc_webhook(connector, payload, signature)
    return result


@router.get("/cdc/events")
async def list_cdc_events(
    connector: str | None = Query(None, description="Filter by connector name"),
    event_type: str | None = Query(None, description="Filter by event type"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
) -> dict[str, Any]:
    """Return stored CDC events with optional filtering and pagination.

    Uses the in-memory store from ``core.cdc.receiver``.
    """
    events = get_stored_events()

    # Apply filters
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
