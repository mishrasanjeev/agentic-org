"""Stripe billing client — checkout, webhooks, subscriptions, usage."""

from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger()

# Guard Stripe import — not required at import time
try:
    import stripe as _stripe
except ImportError:  # pragma: no cover
    _stripe = None  # type: ignore[assignment]

_STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
_STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")


def _get_stripe():
    """Return configured stripe module or raise."""
    if _stripe is None:
        raise RuntimeError("stripe package is not installed — run: pip install stripe")
    _stripe.api_key = _STRIPE_SECRET_KEY
    return _stripe


# ── Checkout ─────────────────────────────────────────────────────────

PLAN_PRICE_MAP: dict[str, str] = {
    "free": "",
    "pro": os.getenv("STRIPE_PRICE_PRO", "price_pro_placeholder"),
    "enterprise": os.getenv("STRIPE_PRICE_ENTERPRISE", "price_ent_placeholder"),
}


def create_checkout_session(
    tenant_id: str,
    plan: str,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a Stripe Checkout session and return the URL."""
    s = _get_stripe()
    price_id = PLAN_PRICE_MAP.get(plan)
    if not price_id:
        raise ValueError(f"Unknown or free plan: {plan}")

    session = s.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"tenant_id": tenant_id, "plan": plan},
    )
    logger.info("stripe_checkout_created", tenant_id=tenant_id, plan=plan)
    return session.url


# ── Webhook handling ─────────────────────────────────────────────────


def _verify_signature(payload: bytes, sig_header: str) -> Any:
    """Verify Stripe webhook signature and return the event."""
    s = _get_stripe()
    return s.Webhook.construct_event(payload, sig_header, _STRIPE_WEBHOOK_SECRET)


def handle_webhook(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Validate signature and process relevant Stripe events.

    Returns dict with event_type and processing result.
    """
    import time as _time

    event = _verify_signature(payload, sig_header)

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
            "processed": False, "rejected": True, "reason": "stale_event",
        }

    event_type: str = event["type"]
    data_obj = event["data"]["object"]

    result: dict[str, Any] = {"event_type": event_type, "processed": False}

    if event_type == "invoice.paid":
        tenant_id = data_obj.get("metadata", {}).get("tenant_id", "")
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
        result.update(processed=True, tenant_id=tenant_id, cancelled=True)
        logger.info("stripe_subscription_cancelled", tenant_id=tenant_id)

    return result


# ── Subscription management ──────────────────────────────────────────


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


# ── Usage query ──────────────────────────────────────────────────────


def get_usage(tenant_id: str) -> dict[str, Any]:
    """Return current usage counters for a tenant.

    Delegates to usage_tracker for the actual Redis-backed numbers.
    """
    from core.billing.usage_tracker import get_usage as _get_usage

    return _get_usage(tenant_id)
