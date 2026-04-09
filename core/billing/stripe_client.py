"""Stripe billing client — checkout sessions, webhooks, subscriptions, portal.

Implements the full Stripe payment lifecycle for outside-India (USD) users:
  1. Create Checkout Session → redirect to Stripe hosted page
  2. Verify session after redirect → GET /billing/callback/stripe
  3. Webhook confirmation → POST /billing/webhook/stripe
  4. Subscription activation on successful payment
  5. Customer Portal for self-service billing management

Stripe MCP server also available for agent workflows:
  npx -y @stripe/mcp --api-key=$STRIPE_SECRET_KEY
  pip install stripe-agent-toolkit

Reference: https://stripe.com/docs/api
"""

from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger()

# Guard Stripe import
try:
    import stripe as _stripe
except ImportError:  # pragma: no cover
    _stripe = None  # type: ignore[assignment]

_STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
_STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Price IDs created in Stripe Dashboard (Products → Pricing)
PLAN_PRICE_MAP: dict[str, str] = {
    "free": "",
    "pro": os.getenv("STRIPE_PRICE_PRO", ""),
    "enterprise": os.getenv("STRIPE_PRICE_ENTERPRISE", ""),
}

# Plan → USD amount in cents (for display / validation only)
PLAN_AMOUNT_USD: dict[str, int] = {
    "pro": 99_00,        # $99/mo
    "enterprise": 499_00,  # $499/mo
}

_API_CALLBACK_URL = os.getenv(
    "STRIPE_CALLBACK_URL",
    "https://app.agenticorg.com/api/v1/billing/callback/stripe",
)


def _get_stripe():
    """Return configured stripe module or raise."""
    if _stripe is None:
        raise RuntimeError("stripe package is not installed — run: pip install stripe")
    _stripe.api_key = _STRIPE_SECRET_KEY
    return _stripe


# ── Create Customer ─────────────────────────────────────────────────


def get_or_create_customer(
    tenant_id: str,
    email: str = "",
    name: str = "",
) -> str:
    """Get existing or create new Stripe Customer for tenant.

    Stores the customer_id mapping for reuse.
    """
    s = _get_stripe()

    # Search for existing customer by metadata
    existing = s.Customer.search(query=f'metadata["tenant_id"]:"{tenant_id}"')
    if existing.data:
        return existing.data[0].id

    # Create new customer
    params: dict[str, Any] = {
        "metadata": {"tenant_id": tenant_id},
    }
    if email:
        params["email"] = email
    if name:
        params["name"] = name

    customer = s.Customer.create(**params)
    logger.info("stripe_customer_created", tenant_id=tenant_id, customer_id=customer.id)
    return customer.id


# ── Checkout Session ────────────────────────────────────────────────


def create_checkout_session(
    tenant_id: str,
    plan: str,
    success_url: str = "",
    cancel_url: str = "",
    customer_email: str = "",
    customer_name: str = "",
) -> dict[str, Any]:
    """Create a Stripe Checkout Session and return session details.

    The frontend should redirect to ``checkout_url``.
    After payment, Stripe redirects to the success_url with session_id.

    Returns
    -------
    dict with session_id, checkout_url, customer_id.
    """
    s = _get_stripe()
    price_id = PLAN_PRICE_MAP.get(plan)
    if not price_id:
        raise ValueError(f"Unknown plan or missing price ID: {plan}")

    # Get or create customer
    customer_id = get_or_create_customer(
        tenant_id=tenant_id,
        email=customer_email,
        name=customer_name,
    )

    # Build callback URLs through the API endpoint for server-side verification
    effective_success_url = (
        success_url
        or f"{_API_CALLBACK_URL}?session_id={{CHECKOUT_SESSION_ID}}&tenant_id={tenant_id}&plan={plan}"
    )
    effective_cancel_url = (
        cancel_url
        or "https://app.agenticorg.com/dashboard/billing?cancelled=1"
    )

    session = s.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=effective_success_url,
        cancel_url=effective_cancel_url,
        metadata={"tenant_id": tenant_id, "plan": plan},
        subscription_data={"metadata": {"tenant_id": tenant_id, "plan": plan}},
    )

    logger.info(
        "stripe_checkout_created",
        tenant_id=tenant_id,
        plan=plan,
        session_id=session.id,
        customer_id=customer_id,
    )

    return {
        "session_id": session.id,
        "checkout_url": session.url,
        "customer_id": customer_id,
    }


# ── Verify Checkout Session ─────────────────────────────────────────


def verify_checkout_session(session_id: str) -> dict[str, Any]:
    """Verify a completed Checkout Session and return its status.

    Called after Stripe redirects back to our success URL.
    """
    s = _get_stripe()
    session = s.checkout.Session.retrieve(session_id)

    tenant_id = session.metadata.get("tenant_id", "")
    plan = session.metadata.get("plan", "")
    payment_status = session.payment_status  # paid | unpaid | no_payment_required
    status = session.status  # open | complete | expired

    is_success = status == "complete" and payment_status == "paid"

    logger.info(
        "stripe_session_verified",
        session_id=session_id,
        tenant_id=tenant_id,
        plan=plan,
        status=status,
        payment_status=payment_status,
    )

    return {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "plan": plan,
        "status": status,
        "payment_status": payment_status,
        "subscription_id": session.subscription,
        "customer_id": session.customer,
        "verified": is_success,
    }


# ── Webhook Handling ────────────────────────────────────────────────


def handle_webhook(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Validate signature and process relevant Stripe events.

    Handles:
      - checkout.session.completed → activate subscription
      - invoice.paid → confirm payment
      - customer.subscription.updated → sync status
      - customer.subscription.deleted → deactivate
    """
    import time as _time

    s = _get_stripe()
    event = s.Webhook.construct_event(payload, sig_header, _STRIPE_WEBHOOK_SECRET)

    # Replay prevention — reject events older than 5 minutes
    event_created = event.get("created", 0)
    age_seconds = _time.time() - event_created
    if age_seconds > 300:
        logger.warning(
            "stripe_webhook_stale_event",
            event_id=event.get("id"),
            event_type=event.get("type"),
            age_seconds=int(age_seconds),
        )
        return {
            "event_type": event.get("type", "unknown"),
            "processed": False,
            "rejected": True,
            "reason": "stale_event",
        }

    event_type: str = event["type"]
    data_obj = event["data"]["object"]

    result: dict[str, Any] = {"event_type": event_type, "processed": False}

    if event_type == "checkout.session.completed":
        tenant_id = data_obj.get("metadata", {}).get("tenant_id", "")
        plan = data_obj.get("metadata", {}).get("plan", "")
        payment_status = data_obj.get("payment_status", "")

        if payment_status == "paid" and tenant_id and plan:
            _activate_subscription(
                tenant_id=tenant_id,
                plan=plan,
                subscription_id=data_obj.get("subscription", ""),
                customer_id=data_obj.get("customer", ""),
            )

        result.update(
            processed=True,
            tenant_id=tenant_id,
            plan=plan,
            payment_status=payment_status,
        )
        logger.info("stripe_checkout_completed", tenant_id=tenant_id, plan=plan)

    elif event_type == "invoice.paid":
        tenant_id = data_obj.get("metadata", {}).get("tenant_id", "")
        # Also check subscription metadata
        if not tenant_id:
            sub_meta = data_obj.get("subscription_details", {}).get("metadata", {})
            tenant_id = sub_meta.get("tenant_id", "")

        result.update(
            processed=True,
            tenant_id=tenant_id,
            amount=data_obj.get("amount_paid", 0),
            currency=data_obj.get("currency", "usd"),
        )
        logger.info("stripe_invoice_paid", tenant_id=tenant_id)

    elif event_type == "customer.subscription.updated":
        tenant_id = data_obj.get("metadata", {}).get("tenant_id", "")
        status = data_obj.get("status", "")
        result.update(
            processed=True,
            tenant_id=tenant_id,
            subscription_status=status,
        )
        logger.info("stripe_subscription_updated", tenant_id=tenant_id, status=status)

    elif event_type == "customer.subscription.deleted":
        tenant_id = data_obj.get("metadata", {}).get("tenant_id", "")
        if tenant_id:
            _deactivate_subscription(tenant_id)
        result.update(processed=True, tenant_id=tenant_id, cancelled=True)
        logger.info("stripe_subscription_cancelled", tenant_id=tenant_id)

    return result


# ── Subscription Management ─────────────────────────────────────────


def _activate_subscription(
    tenant_id: str,
    plan: str,
    subscription_id: str,
    customer_id: str,
) -> None:
    """Upgrade tenant plan after confirmed Stripe payment."""
    from core.billing.usage_tracker import _get_redis

    redis = _get_redis()
    # Store the active plan — both the canonical key read by
    # limits._get_tenant_tier() AND the billing-specific keys.
    redis.set(f"tenant_tier:{tenant_id}", plan)
    redis.set(f"tenant:{tenant_id}:plan", plan)
    redis.set(f"tenant:{tenant_id}:billing_provider", "stripe")
    redis.set(f"tenant:{tenant_id}:stripe_subscription_id", subscription_id)
    redis.set(f"tenant:{tenant_id}:stripe_customer_id", customer_id)

    logger.info(
        "subscription_activated",
        tenant_id=tenant_id,
        plan=plan,
        provider="stripe",
        subscription_id=subscription_id,
    )


def _deactivate_subscription(tenant_id: str) -> None:
    """Downgrade tenant to free plan after cancellation."""
    from core.billing.usage_tracker import _get_redis

    redis = _get_redis()
    redis.set(f"tenant_tier:{tenant_id}", "free")
    redis.set(f"tenant:{tenant_id}:plan", "free")
    redis.delete(f"tenant:{tenant_id}:stripe_subscription_id")

    logger.info("subscription_deactivated", tenant_id=tenant_id)


def cancel_subscription(subscription_id: str) -> bool:
    """Cancel a Stripe subscription immediately."""
    s = _get_stripe()
    try:
        s.Subscription.delete(subscription_id)
        logger.info("stripe_subscription_cancelled", subscription_id=subscription_id)
        return True
    except Exception:
        logger.exception("stripe_cancel_failed", subscription_id=subscription_id)
        return False


# ── Customer Portal ─────────────────────────────────────────────────


def create_portal_session(tenant_id: str, return_url: str = "") -> str:
    """Create a Stripe Customer Portal session for self-service billing.

    Allows users to update payment methods, view invoices,
    and cancel subscriptions.
    """
    s = _get_stripe()

    # Look up customer
    customer_id = ""
    from core.billing.usage_tracker import _get_redis
    redis = _get_redis()
    stored = redis.get(f"tenant:{tenant_id}:stripe_customer_id")
    if stored:
        customer_id = stored if isinstance(stored, str) else stored.decode()

    if not customer_id:
        existing = s.Customer.search(query=f'metadata["tenant_id"]:"{tenant_id}"')
        if existing.data:
            customer_id = existing.data[0].id

    if not customer_id:
        raise ValueError(f"No Stripe customer found for tenant {tenant_id}")

    effective_return_url = return_url or "https://app.agenticorg.com/dashboard/billing"

    session = s.billing_portal.Session.create(
        customer=customer_id,
        return_url=effective_return_url,
    )

    logger.info("stripe_portal_created", tenant_id=tenant_id)
    return session.url


# ── Usage Query ─────────────────────────────────────────────────────


def get_usage(tenant_id: str) -> dict[str, Any]:
    """Return current usage counters for a tenant."""
    from core.billing.usage_tracker import get_usage as _get_usage

    return _get_usage(tenant_id)
