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
    # tenant_id removed — always bound to the authenticated caller's tenant
    plan: str  # pro | enterprise
    success_url: str = ""
    cancel_url: str = ""
    customer_email: str = ""
    customer_name: str = ""


class IndiaSubscribeRequest(BaseModel):
    plan: str  # pro | enterprise
    amount_inr: int | None = None
    customer_email: str = ""
    customer_name: str = ""
    customer_phone: str = ""


class CancelRequest(BaseModel):
    subscription_id: str


class OrderStatusRequest(BaseModel):
    order_id: str


class PortalRequest(BaseModel):
    return_url: str = ""


# ── Plans ────────────────────────────────────────────────────────────


@router.get("/plans")
async def list_plans() -> list[dict[str, Any]]:
    """List available plans with pricing (USD + INR)."""
    from core.billing.limits import PLAN_PRICING

    return PLAN_PRICING


@router.get("/subscription")
async def get_subscription(
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Return the current subscription status for the authenticated tenant.

    Reads from Redis (the source of truth set by the webhook activation).
    """
    from core.billing.usage_tracker import _get_redis

    redis = _get_redis()
    plan_raw = redis.get(f"tenant:{tenant_id}:plan")
    plan = (plan_raw.decode() if isinstance(plan_raw, bytes) else plan_raw) or "free"

    tier_raw = redis.get(f"tenant_tier:{tenant_id}")
    tier = (tier_raw.decode() if isinstance(tier_raw, bytes) else tier_raw) or "free"

    provider_raw = redis.get(f"tenant:{tenant_id}:billing_provider")
    provider = (provider_raw.decode() if isinstance(provider_raw, bytes) else provider_raw) or ""

    order_id_raw = redis.get(f"tenant:{tenant_id}:billing_order_id")
    order_id = (order_id_raw.decode() if isinstance(order_id_raw, bytes) else order_id_raw) or ""

    return {
        "tenant_id": tenant_id,
        "plan": plan,
        "tier": tier,
        "provider": provider,
        "order_id": order_id,
        "is_paid": plan not in ("free", ""),
    }


@router.get("/usage")
async def get_usage_endpoint(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Return current usage counters for the authenticated tenant."""
    from core.billing.usage_tracker import get_usage

    return get_usage(tenant_id)


# ── Stripe Subscribe ─────────────────────────────────────────────────


@router.post("/subscribe")
async def subscribe_stripe(
    body: SubscribeRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Create a Stripe Checkout Session for subscription."""
    from core.billing.stripe_client import create_checkout_session

    try:
        result = create_checkout_session(
            tenant_id=tenant_id,
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
        logger.exception("stripe_checkout_error", tenant_id=tenant_id)
        raise HTTPException(status_code=502, detail="Payment gateway error") from exc


# ── Plural Subscribe (India — Redirect Mode) ────────────────────────


@router.post("/subscribe/india")
async def subscribe_india(
    body: IndiaSubscribeRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Create a Plural payment order and return the hosted checkout URL."""
    from core.billing.pinelabs_client import create_payment_order

    try:
        order = create_payment_order(
            tenant_id=tenant_id,
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
        logger.exception("plural_order_error", tenant_id=tenant_id)
        raise HTTPException(status_code=502, detail="Payment gateway error") from exc


# ── Plural Callback (Redirect Return) ───────────────────────────────


@router.api_route("/callback", methods=["GET", "POST"])
async def plural_callback(request: Request) -> RedirectResponse:
    """Handle Plural hosted checkout redirect back to the app.

    Plural redirects the user's browser here after payment — typically as
    an HTML form POST (most common), but we also accept GET for the
    status-check / refresh flow. Fields can come from either query
    params or the POST form body.

    We look up the order_id via the merchant_ref, verify the actual
    payment status with Plural's API (server-side — cannot be spoofed),
    and then redirect to the frontend callback page with the verified
    result.
    """
    from core.billing.pinelabs_client import get_order_status, lookup_order_details

    # Collect params from both query string and form body (Plural POSTs
    # form-encoded back to the merchant URL).
    params: dict[str, str] = dict(request.query_params)
    if request.method == "POST":
        try:
            form = await request.form()
            for k, v in form.items():
                if k not in params and isinstance(v, str):
                    params[k] = v
        except Exception:
            logger.debug("plural_callback_form_parse_failed")

    merchant_ref = params.get("merchant_ref") or params.get("merchant_order_reference", "")
    tenant_id = params.get("tenant_id", "")
    plan = params.get("plan", "")
    order_id = params.get("order_id", "")
    plural_order_id = params.get("plural_order_id", "")

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
async def create_portal(
    body: PortalRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Create a Stripe Customer Portal session for self-service billing."""
    from core.billing.stripe_client import create_portal_session

    try:
        url = create_portal_session(
            tenant_id=tenant_id,
            return_url=body.return_url,
        )
        return {"portal_url": url}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("stripe_portal_error", tenant_id=tenant_id)
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


# ── Usage + Invoices ────────────────────────────────────────────────
# NOTE: The canonical GET /billing/usage is defined above as
# get_usage_endpoint. The canonical GET /billing/invoices is in
# api/v1/invoices.py (admin-gated). Both legacy duplicates below
# have been removed (RECHECK finding #5).


# ── Cancel ───────────────────────────────────────────────────────────


@router.post("/cancel")
async def cancel_subscription(
    body: CancelRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Cancel a subscription.

    Bound to the authenticated tenant — the caller cannot cancel
    another tenant's subscription.
    """
    from core.billing.usage_tracker import _get_redis

    redis = _get_redis()
    provider_raw = redis.get(f"tenant:{tenant_id}:billing_provider")
    provider = (
        provider_raw.decode() if isinstance(provider_raw, bytes) else (provider_raw or "stripe")
    )

    if provider == "plural":
        redis.set(f"tenant_tier:{tenant_id}", "free")
        redis.set(f"tenant:{tenant_id}:plan", "free")
        redis.delete(f"tenant:{tenant_id}:billing_order_id")
        logger.info("plural_subscription_cancelled", tenant_id=tenant_id)
        return {"cancelled": True, "provider": "plural", "tenant_id": tenant_id}

    # Stripe: resolve subscription_id from server-side state — NEVER
    # trust the caller-supplied ID. One tenant cannot cancel another
    # tenant's subscription even if the Stripe sub ID leaks.
    from core.billing.stripe_client import cancel_subscription as _cancel

    sub_id_raw = redis.get(f"tenant:{tenant_id}:stripe_subscription_id")
    sub_id = (sub_id_raw.decode() if isinstance(sub_id_raw, bytes) else sub_id_raw) or ""
    if not sub_id:
        # Fall back to the caller-supplied ID ONLY if no server-side
        # record exists (legacy tenants that subscribed before this fix).
        sub_id = body.subscription_id
        logger.warning(
            "stripe_cancel_using_caller_sub_id",
            tenant_id=tenant_id,
            sub_id=sub_id,
        )

    if not sub_id:
        raise HTTPException(400, "No active Stripe subscription found for this tenant")

    success = _cancel(sub_id)
    if not success:
        raise HTTPException(status_code=502, detail="Failed to cancel subscription")

    # Downgrade tenant on successful cancellation
    redis.set(f"tenant_tier:{tenant_id}", "free")
    redis.set(f"tenant:{tenant_id}:plan", "free")
    redis.delete(f"tenant:{tenant_id}:stripe_subscription_id")
    return {"cancelled": True, "provider": "stripe", "tenant_id": tenant_id}


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
