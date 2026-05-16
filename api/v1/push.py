"""Web Push notification API endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.push.sender import (
    remove_subscription,
    save_subscription,
    send_push_notification,
)
from core.push.vapid import get_vapid_keys

router = APIRouter()
_log = structlog.get_logger()


# ── Pydantic models ────────────────────────────────────────────────────────

class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscription(BaseModel):
    endpoint: str
    keys: PushKeys


class SubscribeRequest(BaseModel):
    subscription: PushSubscription


class UnsubscribeRequest(BaseModel):
    endpoint: str


class VapidKeyResponse(BaseModel):
    public_key: str


class PushTestResponse(BaseModel):
    sent: int
    failed: int
    stale_removed: int


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/push/vapid-key", response_model=VapidKeyResponse)
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:push.vapid_key.read",
    rate_limit="push-public-config",
    idempotency="read-only",
    audit_event="none-public-push-key-read",
    public_reason="public-browser-vapid-key-no-tenant-data",
)
async def get_vapid_public_key():
    """Return the VAPID public key for push subscription.

    No authentication required — the browser needs this key before
    the user can subscribe to push notifications.
    """
    try:
        public_key, _ = get_vapid_keys()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return VapidKeyResponse(public_key=public_key)


@router.post("/push/subscribe")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="push.subscription.sensitive.write",
    rate_limit="push-subscription-write",
    idempotency="idempotent-upsert-by-endpoint",
    audit_event="push.subscribe",
)
async def subscribe(
    body: SubscribeRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Register a push subscription for the authenticated tenant."""
    subscription_dict = {
        "endpoint": body.subscription.endpoint,
        "keys": {
            "p256dh": body.subscription.keys.p256dh,
            "auth": body.subscription.keys.auth,
        },
    }
    await save_subscription(tenant_id, subscription_dict)
    _log.info("push_subscribed", tenant_id=tenant_id)
    return {"status": "subscribed"}


@router.post("/push/unsubscribe")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="push.subscription.sensitive.write",
    rate_limit="push-subscription-write",
    idempotency="idempotent-delete-by-endpoint",
    audit_event="push.unsubscribe",
)
async def unsubscribe(
    body: UnsubscribeRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Remove a push subscription for the authenticated tenant."""
    await remove_subscription(tenant_id, body.endpoint)
    _log.info("push_unsubscribed", tenant_id=tenant_id)
    return {"status": "unsubscribed"}


@router.post("/push/test", response_model=PushTestResponse)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="push.external_delivery.sensitive.write",
    rate_limit="push-test-send",
    idempotency="not_idempotent-delivers-test-notification",
    audit_event="push.test_send",
)
async def send_test_notification(
    tenant_id: str = Depends(get_current_tenant),
):
    """Send a test push notification to all subscriptions for the tenant."""
    result = await send_push_notification(
        tenant_id=tenant_id,
        title="AgenticOrg Test",
        body="Push notifications are working! You will receive alerts for pending approvals.",
        data={"url": "/dashboard/approvals"},
    )
    return PushTestResponse(**result)
