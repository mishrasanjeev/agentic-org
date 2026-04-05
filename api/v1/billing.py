"""Billing API — plans, subscriptions, usage, webhooks."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = structlog.get_logger()

router = APIRouter(prefix="/billing", tags=["Billing"])


# ── Request / Response models ────────────────────────────────────────


class SubscribeRequest(BaseModel):
    tenant_id: str
    plan: str  # pro | enterprise
    success_url: str = "https://app.agenticorg.com/dashboard/billing?success=1"
    cancel_url: str = "https://app.agenticorg.com/dashboard/billing?cancelled=1"


class IndiaSubscribeRequest(BaseModel):
    tenant_id: str
    plan: str
    amount_inr: int | None = None


class CancelRequest(BaseModel):
    tenant_id: str
    subscription_id: str


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/plans")
async def list_plans() -> list[dict[str, Any]]:
    """List available plans with pricing (USD + INR)."""
    from core.billing.limits import PLAN_PRICING

    return PLAN_PRICING


@router.post("/subscribe")
async def subscribe_stripe(body: SubscribeRequest) -> dict[str, Any]:
    """Create a Stripe Checkout session for subscription."""
    from core.billing.stripe_client import create_checkout_session

    try:
        url = create_checkout_session(
            tenant_id=body.tenant_id,
            plan=body.plan,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
        )
        return {"checkout_url": url}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("stripe_checkout_error", tenant_id=body.tenant_id)
        raise HTTPException(status_code=502, detail="Payment gateway error") from exc


@router.post("/subscribe/india")
async def subscribe_india(body: IndiaSubscribeRequest) -> dict[str, Any]:
    """Create a PineLabs Plural payment order (INR)."""
    from core.billing.pinelabs_client import create_payment_order

    try:
        order = create_payment_order(
            tenant_id=body.tenant_id,
            plan=body.plan,
            amount_inr=body.amount_inr,
        )
        return order
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("pinelabs_order_error", tenant_id=body.tenant_id)
        raise HTTPException(status_code=502, detail="Payment gateway error") from exc


@router.get("/usage")
async def get_usage(tenant_id: str) -> dict[str, Any]:
    """Return current usage counters for a tenant."""
    from core.billing.usage_tracker import get_usage as _get_usage

    return _get_usage(tenant_id)


@router.get("/invoices")
async def list_invoices(tenant_id: str) -> list[dict[str, Any]]:
    """Return invoice history for a tenant.

    In production this queries Stripe's invoice API; here we return
    a structured stub that the UI can consume.
    """
    # TODO: wire to Stripe list invoices API
    return [
        {
            "id": "inv_stub_001",
            "tenant_id": tenant_id,
            "date": "2026-03-01",
            "amount": 9900,
            "currency": "usd",
            "status": "paid",
            "plan": "pro",
        }
    ]


@router.post("/cancel")
async def cancel_subscription(body: CancelRequest) -> dict[str, Any]:
    """Cancel a subscription."""
    from core.billing.stripe_client import cancel_subscription as _cancel

    success = _cancel(body.subscription_id)
    if not success:
        raise HTTPException(status_code=502, detail="Failed to cancel subscription")
    return {"cancelled": True, "subscription_id": body.subscription_id}


# ── Webhooks ─────────────────────────────────────────────────────────


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    """Handle Stripe webhook callbacks."""
    from core.billing.stripe_client import handle_webhook

    body = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    try:
        result = handle_webhook(body, sig)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("stripe_webhook_error")
        raise HTTPException(status_code=400, detail="Webhook processing failed") from exc


@router.post("/webhook/pinelabs")
async def pinelabs_webhook(request: Request) -> dict[str, Any]:
    """Handle PineLabs Plural webhook callbacks."""
    from core.billing.pinelabs_client import handle_webhook

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    sig = request.headers.get("X-Plural-Signature", "")
    if not sig:
        raise HTTPException(
            status_code=400, detail="Missing X-Plural-Signature header"
        )

    try:
        result = handle_webhook(payload, sig)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("pinelabs_webhook_error")
        raise HTTPException(status_code=400, detail="Webhook processing failed") from exc
