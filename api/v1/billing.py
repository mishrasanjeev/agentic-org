"""Billing API — plans, subscriptions, usage, webhooks, payment callbacks.

Supports two payment gateways:
  - Stripe (USD, global)
  - PineLabs Plural (INR, India — hosted checkout / redirect mode)

Plural flow:
  1. POST /billing/subscribe/india → returns challenge_url
  2. Frontend redirects user to challenge_url (Plural hosted checkout)
  3. User pays via CARD / UPI / NETBANKING / WALLET / EMI
  4. Plural redirects back to GET /billing/callback
  5. POST /billing/webhook/plural receives async confirmation
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.deps import get_current_tenant

logger = structlog.get_logger()

router = APIRouter(prefix="/billing", tags=["Billing"])


# ── Request / Response models ────────────────────────────────────────


class SubscribeRequest(BaseModel):
    tenant_id: str
    plan: str  # pro | enterprise
    success_url: str = ""  # defaults to API callback for server-side verification
    cancel_url: str = ""
    customer_email: str = ""
    customer_name: str = ""


class IndiaSubscribeRequest(BaseModel):
    tenant_id: str
    plan: str  # pro | enterprise
    amount_inr: int | None = None
    customer_email: str = ""
    customer_name: str = ""
    customer_phone: str = ""


class CancelRequest(BaseModel):
    tenant_id: str
    subscription_id: str


class OrderStatusRequest(BaseModel):
    order_id: str


class PortalRequest(BaseModel):
    tenant_id: str
    return_url: str = ""


# ── Plans ────────────────────────────────────────────────────────────


@router.get("/plans")
async def list_plans() -> list[dict[str, Any]]:
    """List available plans with pricing (USD + INR)."""
    from core.billing.limits import PLAN_PRICING

    return PLAN_PRICING


# ── Stripe Subscribe ─────────────────────────────────────────────────


@router.post("/subscribe")
async def subscribe_stripe(body: SubscribeRequest) -> dict[str, Any]:
    """Create a Stripe Checkout Session for subscription.

    The frontend should redirect to ``checkout_url``.
    After payment, Stripe redirects to the API callback for server-side
    verification, then to the frontend callback page.
    """
    from core.billing.stripe_client import create_checkout_session

    try:
        result = create_checkout_session(
            tenant_id=body.tenant_id,
            plan=body.plan,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            customer_email=body.customer_email,
            customer_name=body.customer_name,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("stripe_checkout_error", tenant_id=body.tenant_id)
        raise HTTPException(status_code=502, detail="Payment gateway error") from exc


# ── Plural Subscribe (India — Redirect Mode) ────────────────────────


@router.post("/subscribe/india")
async def subscribe_india(body: IndiaSubscribeRequest) -> dict[str, Any]:
    """Create a Plural payment order and return the hosted checkout URL.

    The frontend should redirect the user to ``challenge_url``.
    All payment methods are enabled: CARD, UPI, NETBANKING, WALLET,
    CREDIT_EMI, DEBIT_EMI.
    """
    from core.billing.pinelabs_client import create_payment_order

    try:
        order = create_payment_order(
            tenant_id=body.tenant_id,
            plan=body.plan,
            amount_inr=body.amount_inr,
            customer_email=body.customer_email,
            customer_name=body.customer_name,
            customer_phone=body.customer_phone,
        )
        return order
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("plural_order_error", tenant_id=body.tenant_id)
        raise HTTPException(status_code=502, detail="Payment gateway error") from exc


# ── Plural Callback (Redirect Return) ───────────────────────────────


@router.get("/callback")
async def plural_callback(
    merchant_ref: str = "",
    tenant_id: str = "",
    plan: str = "",
    order_id: str = "",
    plural_order_id: str = "",
) -> RedirectResponse:
    """Handle Plural hosted checkout redirect back to the app.

    Plural redirects the user's browser here after payment.  We look up
    the order_id via the merchant_ref, verify the actual payment status
    with Plural's API (server-side — cannot be spoofed), and then
    redirect to the frontend callback page with the verified result.
    """
    from core.billing.pinelabs_client import get_order_status, lookup_order_details

    # Resolve order details from stored mapping or params
    effective_order_id = order_id or plural_order_id
    if merchant_ref:
        stored = lookup_order_details(merchant_ref)
        if not effective_order_id:
            effective_order_id = stored.get("order_id", "")
        if not tenant_id:
            tenant_id = stored.get("tenant_id", "")
        if not plan:
            plan = stored.get("plan", "")

    verified_status = "pending"

    # Server-side verification: call Plural API to get real status
    if effective_order_id:
        try:
            order_data = get_order_status(effective_order_id)
            actual_status = order_data.get("status", "")

            if actual_status in ("PROCESSED", "AUTHORIZED"):
                verified_status = "success"
            elif actual_status in ("FAILED", "CANCELLED"):
                verified_status = "failed"
            else:
                verified_status = "pending"

            logger.info(
                "plural_callback_verified",
                order_id=effective_order_id,
                merchant_ref=merchant_ref,
                plural_status=actual_status,
                verified_status=verified_status,
            )
        except Exception:
            logger.exception(
                "plural_callback_verify_failed", order_id=effective_order_id
            )
            verified_status = "pending"
    else:
        logger.warning(
            "plural_callback_no_order_id",
            merchant_ref=merchant_ref,
            tenant_id=tenant_id,
        )

    # Redirect to frontend callback page with server-verified result
    fe_url = "/dashboard/billing/callback"
    params = f"?payment={verified_status}&provider=plural"
    if tenant_id:
        params += f"&tenant_id={tenant_id}"
    if plan:
        params += f"&plan={plan}"
    if effective_order_id:
        params += f"&order_id={effective_order_id}"

    return RedirectResponse(url=f"{fe_url}{params}", status_code=303)


# ── Stripe Callback (Checkout Return) ────────────────────────────────


@router.get("/callback/stripe")
async def stripe_callback(
    session_id: str = "",
    tenant_id: str = "",
    plan: str = "",
) -> RedirectResponse:
    """Handle Stripe Checkout redirect back to the app.

    After the user completes payment on Stripe's hosted page, they are
    redirected here with the session_id.  We verify the session status
    server-side and redirect to the frontend callback page.
    """
    verified_status = "pending"

    if session_id:
        try:
            from core.billing.stripe_client import verify_checkout_session

            session_data = verify_checkout_session(session_id)

            if session_data.get("verified"):
                verified_status = "success"
                tenant_id = tenant_id or session_data.get("tenant_id", "")
                plan = plan or session_data.get("plan", "")
            elif session_data.get("status") == "expired":
                verified_status = "failed"
            else:
                verified_status = "pending"

            logger.info(
                "stripe_callback_verified",
                session_id=session_id,
                verified_status=verified_status,
            )
        except Exception:
            logger.exception("stripe_callback_verify_failed", session_id=session_id)
            verified_status = "pending"
    else:
        logger.warning("stripe_callback_no_session_id")

    # Redirect to frontend callback page
    fe_url = "/dashboard/billing/callback"
    params = f"?payment={verified_status}&provider=stripe"
    if tenant_id:
        params += f"&tenant_id={tenant_id}"
    if plan:
        params += f"&plan={plan}"
    if session_id:
        params += f"&session_id={session_id}"

    return RedirectResponse(url=f"{fe_url}{params}", status_code=303)


# ── Customer Portal ──────────────────────────────────────────────────


@router.post("/portal")
async def create_portal(body: PortalRequest) -> dict[str, Any]:
    """Create a Stripe Customer Portal session for self-service billing.

    Users can update payment methods, view invoices, and cancel.
    """
    from core.billing.stripe_client import create_portal_session

    try:
        url = create_portal_session(
            tenant_id=body.tenant_id,
            return_url=body.return_url,
        )
        return {"portal_url": url}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("stripe_portal_error", tenant_id=body.tenant_id)
        raise HTTPException(status_code=502, detail="Portal creation failed") from exc


# ── Order Status Check ───────────────────────────────────────────────


@router.post("/order-status")
async def check_order_status(body: OrderStatusRequest) -> dict[str, Any]:
    """Check the status of a Plural payment order."""
    from core.billing.pinelabs_client import get_order_status

    try:
        return get_order_status(body.order_id)
    except Exception as exc:
        logger.exception("plural_status_error", order_id=body.order_id)
        raise HTTPException(status_code=502, detail="Failed to check order status") from exc


# ── Usage ────────────────────────────────────────────────────────────


@router.get("/usage")
async def get_usage(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Return current usage counters for a tenant."""
    from core.billing.usage_tracker import get_usage as _get_usage

    return _get_usage(tenant_id)


# ── Invoices ─────────────────────────────────────────────────────────


@router.get("/invoices")
async def list_invoices(tenant_id: str = Depends(get_current_tenant)) -> list[dict[str, Any]]:
    """Return invoice history for a tenant."""
    # TODO: wire to DB billing_invoices table
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


# ── Cancel ───────────────────────────────────────────────────────────


@router.post("/cancel")
async def cancel_subscription(body: CancelRequest) -> dict[str, Any]:
    """Cancel a subscription.

    Checks the tenant's billing provider (Stripe or Plural) and routes
    the cancellation accordingly.  Plural orders are one-time payments
    without recurring subscriptions, so cancellation only applies to Stripe.
    """
    from core.billing.usage_tracker import _get_redis

    redis = _get_redis()
    provider_raw = redis.get(f"tenant:{body.tenant_id}:billing_provider")
    provider = (
        provider_raw.decode() if isinstance(provider_raw, bytes) else (provider_raw or "stripe")
    )

    if provider == "plural":
        # Plural is one-time payment — just downgrade to free
        redis.set(f"tenant_tier:{body.tenant_id}", "free")
        redis.set(f"tenant:{body.tenant_id}:plan", "free")
        redis.delete(f"tenant:{body.tenant_id}:billing_order_id")
        logger.info("plural_subscription_cancelled", tenant_id=body.tenant_id)
        return {"cancelled": True, "provider": "plural", "tenant_id": body.tenant_id}

    # Stripe: cancel the actual subscription
    from core.billing.stripe_client import cancel_subscription as _cancel

    success = _cancel(body.subscription_id)
    if not success:
        raise HTTPException(status_code=502, detail="Failed to cancel subscription")
    return {"cancelled": True, "subscription_id": body.subscription_id, "provider": "stripe"}


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


@router.post("/webhook/plural")
async def plural_webhook(request: Request) -> dict[str, Any]:
    """Handle PineLabs Plural webhook callbacks.

    Plural sends three signature headers:
      - webhook-id: unique event identifier
      - webhook-timestamp: unix timestamp in seconds
      - webhook-signature: base64 HMAC-SHA256 with "v1," prefix
    """
    from core.billing.pinelabs_client import handle_webhook

    raw_body = await request.body()
    headers = {
        "webhook-id": request.headers.get("webhook-id", ""),
        "webhook-timestamp": request.headers.get("webhook-timestamp", ""),
        "webhook-signature": request.headers.get("webhook-signature", ""),
    }

    if not all(headers.values()):
        raise HTTPException(
            status_code=400,
            detail="Missing Plural webhook headers (webhook-id, webhook-timestamp, webhook-signature)",
        )

    try:
        result = handle_webhook(raw_body, headers)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("plural_webhook_error")
        raise HTTPException(status_code=400, detail="Webhook processing failed") from exc
