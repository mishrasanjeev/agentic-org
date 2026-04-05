"""PineLabs Plural (India payments) billing client.

Follows the same interface pattern as stripe_client but for INR payments
via PineLabs Plural gateway — popular in the Indian enterprise market.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

import structlog

logger = structlog.get_logger()

# Guard requests import
try:
    import httpx as _httpx
except ImportError:  # pragma: no cover
    _httpx = None  # type: ignore[assignment]

_PINELABS_MERCHANT_ID = os.getenv("PINELABS_MERCHANT_ID", "")
_PINELABS_ACCESS_CODE = os.getenv("PINELABS_ACCESS_CODE", "")
_PINELABS_SECRET = os.getenv("PINELABS_SECRET", "")
_PINELABS_BASE_URL = os.getenv(
    "PINELABS_BASE_URL", "https://api.pluralonline.com/api/v1"
)

# Plan → INR amount (paise)
PLAN_AMOUNT_INR: dict[str, int] = {
    "pro": 9_999_00,  # INR 9,999/mo
    "enterprise": 49_999_00,  # INR 49,999/mo
}


def _get_http_client():
    if _httpx is None:
        raise RuntimeError("httpx package is not installed — run: pip install httpx")
    return _httpx


def _compute_hash(data: str) -> str:
    """HMAC-SHA256 hash using PineLabs secret."""
    return hmac.new(
        _PINELABS_SECRET.encode(), data.encode(), hashlib.sha256
    ).hexdigest()


# ── Create payment order ─────────────────────────────────────────────


def create_payment_order(
    tenant_id: str,
    plan: str,
    amount_inr: int | None = None,
) -> dict[str, Any]:
    """Create a PineLabs Plural payment order and return order details.

    Parameters
    ----------
    tenant_id : str
        The tenant initiating the payment.
    plan : str
        Plan name (pro / enterprise).
    amount_inr : int | None
        Amount in paise. If None, uses PLAN_AMOUNT_INR lookup.

    Returns
    -------
    dict with order_id, payment_url, amount, currency.
    """
    http = _get_http_client()
    amount = amount_inr or PLAN_AMOUNT_INR.get(plan, 0)
    if not amount:
        raise ValueError(f"Unknown plan or zero amount: {plan}")

    payload = {
        "merchant_id": _PINELABS_MERCHANT_ID,
        "merchant_access_code": _PINELABS_ACCESS_CODE,
        "order_id": f"ao_{tenant_id}_{plan}",
        "amount": amount,
        "currency_code": "INR",
        "redirect_url": os.getenv(
            "PINELABS_REDIRECT_URL", "https://app.agenticorg.com/billing/callback"
        ),
        "metadata": {"tenant_id": tenant_id, "plan": plan},
    }

    hash_str = f"{payload['merchant_id']}|{payload['order_id']}|{payload['amount']}"
    payload["hash"] = _compute_hash(hash_str)

    resp = http.post(
        f"{_PINELABS_BASE_URL}/order/create",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    logger.info(
        "pinelabs_order_created",
        tenant_id=tenant_id,
        plan=plan,
        order_id=data.get("order_id"),
    )
    return {
        "order_id": data.get("order_id", payload["order_id"]),
        "payment_url": data.get("redirect_url", data.get("payment_url", "")),
        "amount": amount,
        "currency": "INR",
    }


# ── Verify payment ──────────────────────────────────────────────────


def verify_payment(order_id: str) -> bool:
    """Verify that a PineLabs payment was successful.

    Calls the PineLabs status API and checks for success status.
    """
    http = _get_http_client()

    payload = {
        "merchant_id": _PINELABS_MERCHANT_ID,
        "merchant_access_code": _PINELABS_ACCESS_CODE,
        "order_id": order_id,
    }
    hash_str = f"{payload['merchant_id']}|{payload['order_id']}"
    payload["hash"] = _compute_hash(hash_str)

    try:
        resp = http.post(
            f"{_PINELABS_BASE_URL}/order/status",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("order_status", "").lower()
        success = status in ("charged", "success", "captured")
        logger.info(
            "pinelabs_payment_verified",
            order_id=order_id,
            status=status,
            success=success,
        )
        return success
    except Exception:
        logger.exception("pinelabs_verify_failed", order_id=order_id)
        return False


# ── Webhook handler ──────────────────────────────────────────────────


def handle_webhook(payload: dict[str, Any], sig_header: str) -> dict[str, Any]:
    """Validate PineLabs callback signature and process the event.

    Parameters
    ----------
    payload : dict
        Parsed JSON body from PineLabs callback.
    sig_header : str
        Value of X-Plural-Signature header.

    Returns
    -------
    dict with event details and processing result.
    """
    # Verify signature
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    expected = _compute_hash(canonical)
    if not hmac.compare_digest(expected, sig_header):
        raise ValueError("Invalid PineLabs webhook signature")

    order_id = payload.get("order_id", "")
    status = payload.get("order_status", "").lower()
    tenant_id = payload.get("metadata", {}).get("tenant_id", "")

    result: dict[str, Any] = {
        "order_id": order_id,
        "status": status,
        "tenant_id": tenant_id,
        "processed": status in ("charged", "success", "captured"),
    }
    logger.info("pinelabs_webhook_processed", **result)
    return result
