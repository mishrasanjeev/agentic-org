"""Web Push notification API endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_current_tenant, get_current_user
from api.route_metadata import route_meta
from core.push.sender import (
    remove_subscription,
    save_subscription,
    send_push_notification_for_user,
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


def _user_id(user: dict) -> str:
    """Fail closed: push subscriptions are per user, so a subject is required."""
    user_id = str(user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(401, "Authenticated user subject required")
    return user_id


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
    user: dict = Depends(get_current_user),
):
    """Register a push subscription for the authenticated user in their tenant."""
    user_id = _user_id(user)
    subscription_dict = {
        "endpoint": body.subscription.endpoint,
        "keys": {
            "p256dh": body.subscription.keys.p256dh,
            "auth": body.subscription.keys.auth,
        },
    }
    await save_subscription(tenant_id, subscription_dict, user_id=user_id)
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
    user: dict = Depends(get_current_user),
):
    """Remove one of the authenticated user's push subscriptions."""
    await remove_subscription(tenant_id, body.endpoint, user_id=_user_id(user))
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
    user: dict = Depends(get_current_user),
):
    """Send a test push notification to the caller's own subscriptions."""
    result = await send_push_notification_for_user(
        tenant_id=tenant_id,
        user_id=_user_id(user),
        title="AgenticOrg Test",
        body="Push notifications are working! You will receive alerts for pending approvals.",
        data={"url": "/dashboard/approvals"},
    )
    return PushTestResponse(**result)
