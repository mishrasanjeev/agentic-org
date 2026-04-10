"""PineLabs Plural payment client — Plural API v1 (hosted checkout / redirect mode).

Implements the full Plural payment lifecycle:
  1. OAuth token → POST /api/auth/v1/token
  2. Create order → POST /api/pay/v1/orders (returns challenge_url for redirect)
  3. Get order status → GET /api/pay/v1/orders/{order_id}
  4. Webhook verification → HMAC-SHA256 (webhook-id . webhook-timestamp . body)

All payment methods enabled in redirect mode:
  CARD, UPI, NETBANKING, WALLET, CREDIT_EMI, DEBIT_EMI

Reference: https://developer.pinelabsonline.com/
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

try:
    import httpx as _httpx
except ImportError:  # pragma: no cover
    _httpx = None  # type: ignore[assignment]

logger = structlog.get_logger()

# ── Configuration ───────────────────────────────────────────────────

_CLIENT_ID = os.getenv("PLURAL_CLIENT_ID", "")
_CLIENT_SECRET = os.getenv("PLURAL_CLIENT_SECRET", "")
_WEBHOOK_SECRET = os.getenv("PLURAL_WEBHOOK_SECRET", "")

_ENV = os.getenv("PLURAL_ENV", "sandbox")  # "sandbox" or "production"

_BASE_URLS = {
    "sandbox": "https://pluraluat.v2.pinepg.in/api",
    "production": "https://api.pluralpay.in/api",
}

_BASE_URL = _BASE_URLS.get(_ENV, _BASE_URLS["sandbox"])

_APP_BASE_URL = os.getenv("AGENTICORG_APP_URL", "https://app.agenticorg.com")

_API_CALLBACK_URL = os.getenv(
    "PLURAL_CALLBACK_URL", f"{_APP_BASE_URL}/api/v1/billing/callback"
)

# All payment methods supported in redirect mode
ALL_PAYMENT_METHODS = [
    "CARD",
    "UPI",
    "NETBANKING",
    "WALLET",
    "CREDIT_EMI",
    "DEBIT_EMI",
]

# Plan → amount in paise (smallest currency unit)
PLAN_AMOUNT_INR: dict[str, int] = {
    "pro": 9_999_00,       # INR 9,999
    "enterprise": 49_999_00,  # INR 49,999
}

# ── Order mapping (merchant_ref → order_id) ─────────────────────────
# Kept in memory; for multi-process deploys, move to Redis.
_order_map: dict[str, Any] = {}


def store_order_mapping(
    merchant_ref: str, order_id: str, tenant_id: str = "", plan: str = "",
) -> None:
    """Store merchant_ref → order details mapping for callback lookup."""
    _order_map[merchant_ref] = {
        "order_id": order_id,
        "tenant_id": tenant_id,
        "plan": plan,
    }


def lookup_order_id(merchant_ref: str) -> str:
    """Look up Plural order_id from merchant_order_reference."""
    entry = _order_map.get(merchant_ref, {})
    if isinstance(entry, dict):
        return entry.get("order_id", "")
    return entry  # backwards compat if raw string


def lookup_order_details(merchant_ref: str) -> dict[str, str]:
    """Look up full order details from merchant_order_reference."""
    entry = _order_map.get(merchant_ref, {})
    if isinstance(entry, dict):
        return entry
    return {"order_id": entry} if entry else {}


# ── Token cache ─────────────────────────────────────────────────────

_token_cache: dict[str, Any] = {"access_token": "", "expires_at": 0.0}


def _get_http() -> Any:
    if _httpx is None:
        raise RuntimeError("httpx is required — run: pip install httpx")
    return _httpx


def _get_access_token() -> str:
    """Obtain or reuse a cached OAuth access token from Plural."""
    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["access_token"]

    http = _get_http()
    resp = http.post(
        f"{_BASE_URL}/auth/v1/token",
        json={
            "client_id": _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    # Parse expires_at ISO string or fall back to 50-minute window
    expires_at_str = data.get("expires_at", "")
    if expires_at_str:
        try:
            dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            _token_cache["expires_at"] = dt.timestamp()
        except (ValueError, TypeError):
            _token_cache["expires_at"] = now + 3000
    else:
        _token_cache["expires_at"] = now + 3000

    logger.info("plural_token_acquired", expires_at=expires_at_str)
    return _token_cache["access_token"]


def _auth_headers() -> dict[str, str]:
    """Build standard headers for Plural API calls."""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_get_access_token()}",
        "Request-Timestamp": datetime.now(UTC).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z"),
        "Request-ID": str(uuid.uuid4()),
    }


# ── Create Order (Hosted Checkout / Redirect) ──────────────────────


def create_payment_order(
    tenant_id: str,
    plan: str,
    amount_inr: int | None = None,
    customer_email: str = "",
    customer_name: str = "",
    customer_phone: str = "",
) -> dict[str, Any]:
    """Create a Plural order and return the hosted checkout redirect URL.

    The response ``challenge_url`` is where the user should be redirected.
    Plural handles the payment page with all enabled methods (Cards, UPI,
    Net Banking, Wallets, EMI).

    Parameters
    ----------
    tenant_id : str
        Tenant initiating the payment.
    plan : str
        Plan name — ``pro`` or ``enterprise``.
    amount_inr : int | None
        Override amount in paise.  Falls back to PLAN_AMOUNT_INR.
    customer_email, customer_name, customer_phone : str
        Optional customer details for the checkout page.

    Returns
    -------
    dict with order_id, challenge_url, amount, currency, status.
    """
    http = _get_http()
    amount = amount_inr or PLAN_AMOUNT_INR.get(plan, 0)
    if not amount:
        raise ValueError(f"Unknown plan or zero amount: {plan}")

    merchant_ref = f"ao_{tenant_id}_{plan}_{uuid.uuid4().hex[:8]}"

    cb_params = f"merchant_ref={merchant_ref}&tenant_id={tenant_id}&plan={plan}"
    body: dict[str, Any] = {
        "merchant_order_reference": merchant_ref,
        "order_amount": {
            "value": amount,
            "currency": "INR",
        },
        "pre_auth": False,
        "allowed_payment_methods": ALL_PAYMENT_METHODS,
        "notes": f"AgenticOrg {plan} subscription — tenant {tenant_id}",
        "callback_url": f"{_API_CALLBACK_URL}?{cb_params}",
        "failure_callback_url": f"{_API_CALLBACK_URL}?{cb_params}",
    }

    # Attach customer details if provided
    if customer_email or customer_name or customer_phone:
        purchase_details: dict[str, Any] = {"customer": {}}
        if customer_email:
            purchase_details["customer"]["email_id"] = customer_email
        if customer_name:
            parts = customer_name.split(" ", 1)
            purchase_details["customer"]["first_name"] = parts[0]
            if len(parts) > 1:
                purchase_details["customer"]["last_name"] = parts[1]
        if customer_phone:
            purchase_details["customer"]["mobile_number"] = customer_phone
            purchase_details["customer"]["country_code"] = "91"
        body["purchase_details"] = purchase_details

    # Use the hosted checkout endpoint (not /pay/v1/orders which is seamless mode)
    resp = http.post(
        f"{_BASE_URL}/checkout/v1/orders",
        json=body,
        headers=_auth_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    order_id = data.get("order_id", "")
    redirect_url = data.get("redirect_url", "")

    # Store mapping so the callback endpoint can look up the order
    if order_id:
        store_order_mapping(merchant_ref, order_id, tenant_id, plan)

    logger.info(
        "plural_order_created",
        tenant_id=tenant_id,
        plan=plan,
        order_id=order_id,
        merchant_ref=merchant_ref,
        amount_paise=amount,
        redirect_url=redirect_url[:80] if redirect_url else "",
    )

    return {
        "order_id": order_id,
        "merchant_order_reference": merchant_ref,
        "challenge_url": redirect_url,
        "amount": amount,
        "currency": "INR",
        "status": "CREATED",
        "allowed_payment_methods": ALL_PAYMENT_METHODS,
    }


# ── Get Order Status ────────────────────────────────────────────────


def get_order_status(order_id: str) -> dict[str, Any]:
    """Check the status of a Plural order.

    Returns
    -------
    dict with order_id, status, payments, amount details.
    """
    http = _get_http()
    resp = http.get(
        f"{_BASE_URL}/pay/v1/orders/{order_id}",
        headers=_auth_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.json()
    # Plural wraps the response in a "data" key
    data = raw.get("data", raw)

    status = data.get("status", "UNKNOWN")
    logger.info("plural_order_status", order_id=order_id, status=status)

    return {
        "order_id": data.get("order_id", order_id),
        "merchant_order_reference": data.get("merchant_order_reference", ""),
        "status": status,
        "order_amount": data.get("order_amount", {}),
        "payments": data.get("payments", []),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
    }


# ── Webhook Signature Verification ─────────────────────────────────


def verify_webhook_signature(
    raw_body: bytes,
    webhook_id: str,
    webhook_timestamp: str,
    webhook_signature: str,
) -> bool:
    """Verify Plural webhook HMAC-SHA256 signature.

    Plural signs: ``{webhook_id}.{webhook_timestamp}.{raw_body}``
    using the merchant webhook secret (base64-decoded).
    The signature header has a ``v1,`` prefix.

    Parameters
    ----------
    raw_body : bytes
        The unparsed request body.
    webhook_id, webhook_timestamp, webhook_signature : str
        Values from the webhook-id, webhook-timestamp, webhook-signature headers.

    Returns
    -------
    True if signature is valid.
    """
    if not _WEBHOOK_SECRET:
        logger.warning("plural_webhook_secret_not_set")
        return False

    # Decode the base64 secret
    try:
        secret_bytes = base64.b64decode(_WEBHOOK_SECRET)
    except Exception:
        logger.exception("plural_webhook_secret_decode_failed")
        return False

    # Construct the signed content
    signed_content = f"{webhook_id}.{webhook_timestamp}.".encode() + raw_body
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode()

    # The signature header may contain multiple signatures: "v1,<sig1> v1,<sig2>"
    signatures = webhook_signature.split(" ")
    for sig in signatures:
        sig_value = sig.removeprefix("v1,")
        if hmac.compare_digest(expected, sig_value):
            return True

    return False


def handle_webhook(raw_body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    """Validate and process a Plural webhook event.

    Parameters
    ----------
    raw_body : bytes
        Unparsed request body.
    headers : dict
        Request headers containing webhook-id, webhook-timestamp, webhook-signature.

    Returns
    -------
    dict with order details and processing result.
    """
    import json

    webhook_id = headers.get("webhook-id", "")
    webhook_timestamp = headers.get("webhook-timestamp", "")
    webhook_signature = headers.get("webhook-signature", "")

    if not all([webhook_id, webhook_timestamp, webhook_signature]):
        raise ValueError("Missing webhook signature headers")

    # Validate timestamp (reject events older than 5 minutes)
    try:
        ts = int(webhook_timestamp)
        if abs(time.time() - ts) > 300:
            raise ValueError("Webhook timestamp too old — possible replay attack")
    except (ValueError, TypeError) as exc:
        if "replay" in str(exc):
            raise
        raise ValueError("Invalid webhook timestamp") from exc

    if not verify_webhook_signature(raw_body, webhook_id, webhook_timestamp, webhook_signature):
        raise ValueError("Invalid Plural webhook signature")

    payload = json.loads(raw_body)
    order_id = payload.get("order_id", "")
    status = payload.get("status", "").upper()
    merchant_ref = payload.get("merchant_order_reference", "")

    # Resolve tenant_id and plan from stored mapping first, then fall
    # back to parsing the merchant_ref (only safe if tenant_id has no underscores).
    stored = lookup_order_details(merchant_ref)
    tenant_id = stored.get("tenant_id", "")
    plan = stored.get("plan", "")

    if not tenant_id and merchant_ref.startswith("ao_"):
        # Fallback: format is ao_{tenant}_{plan}_{uuid8}
        # Use rsplit to peel off uuid and plan from the right side
        without_prefix = merchant_ref[3:]  # strip "ao_"
        rparts = without_prefix.rsplit("_", 2)  # [tenant..., plan, uuid8]
        if len(rparts) == 3:
            tenant_id = rparts[0]
            plan = rparts[1]

    is_success = status in ("PROCESSED", "AUTHORIZED", "CAPTURED")

    # Activate subscription on successful payment
    if is_success and tenant_id and plan:
        try:
            _activate_subscription(tenant_id, plan, order_id)
        except Exception:
            logger.exception(
                "plural_subscription_activation_failed",
                tenant_id=tenant_id, plan=plan, order_id=order_id,
            )

    result = {
        "order_id": order_id,
        "merchant_order_reference": merchant_ref,
        "status": status,
        "tenant_id": tenant_id,
        "plan": plan,
        "processed": is_success,
        "webhook_id": webhook_id,
    }

    logger.info("plural_webhook_processed", **result)
    return result


def _activate_subscription(tenant_id: str, plan: str, order_id: str) -> None:
    """Upgrade tenant plan after confirmed payment.

    Creates a billing_subscription record and updates the tenant tier.
    """
    from core.billing.usage_tracker import _get_redis

    redis = _get_redis()
    # Store the active plan — both the canonical key read by
    # limits._get_tenant_tier() AND the billing-specific keys.
    redis.set(f"tenant_tier:{tenant_id}", plan)
    redis.set(f"tenant:{tenant_id}:plan", plan)
    redis.set(f"tenant:{tenant_id}:billing_provider", "plural")
    redis.set(f"tenant:{tenant_id}:billing_order_id", order_id)

    logger.info(
        "subscription_activated",
        tenant_id=tenant_id,
        plan=plan,
        order_id=order_id,
        provider="plural",
    )
