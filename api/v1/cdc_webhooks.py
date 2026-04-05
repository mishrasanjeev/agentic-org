"""Generic CDC webhook endpoint — receives change-data-capture events from any connector."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from core.cdc.receiver import handle_cdc_webhook

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
