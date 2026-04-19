"""Webhook endpoints for email event tracking (open/click/bounce).

Every externally reachable webhook handler here fails closed when the
provider's verification secret is missing, per SECURITY_AUDIT-2026-04-19
HIGH-04. Set ``AGENTICORG_WEBHOOK_ALLOW_UNSIGNED=1`` only in local dev.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request

logger = structlog.get_logger()

router = APIRouter()


def _dev_allow_unsigned() -> bool:
    return os.getenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED") == "1"


def _get_redis():
    """Return an async Redis client."""
    import os

    import redis.asyncio as aioredis

    url = os.getenv("AGENTICORG_REDIS_URL", "redis://localhost:6379/1")
    return aioredis.from_url(url, decode_responses=True)


async def _store_email_event(
    campaign_id: str,
    email: str,
    event_type: str,
    timestamp: str | int,
    extra: dict[str, Any] | None = None,
) -> None:
    """Store event in Redis and check for waiting workflows."""
    r = _get_redis()
    try:
        # Store in hash: email_events:{campaign_id}:{email}
        key = f"email_events:{campaign_id}:{email}"
        mapping: dict[str, str] = {
            event_type: "true",
            f"{event_type}_timestamp": str(timestamp),
        }
        if extra:
            for k, v in extra.items():
                mapping[k] = str(v)
        await r.hset(key, mapping=mapping)

        # Check for workflow event waits
        # Keys look like: wfwait_event:email.{event}:{run_id}:{step_id}
        pattern = f"wfwait_event:email.{event_type}:*"
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor=cursor, match=pattern, count=100)
            for wait_key in keys:
                parts = wait_key.split(":")
                if len(parts) >= 4:
                    run_id = parts[2]
                    step_id = parts[3]
                    logger.info(
                        "workflow_event_match",
                        event_type=event_type,
                        email=email,
                        run_id=run_id,
                        step_id=step_id,
                    )
                    # Trigger workflow resumption via Celery
                    from core.tasks.workflow_tasks import resume_workflow_wait

                    resume_workflow_wait.delay(run_id, step_id)
                    # Remove the wait key so it only fires once
                    await r.delete(wait_key)
            if cursor == 0:
                break
    finally:
        await r.aclose()


def _verify_sendgrid_signature(
    payload: bytes,
    signature: str,
    timestamp: str,
    public_key: str | None = None,
) -> bool:
    """Verify SendGrid Event Webhook signature.

    Uses HMAC-SHA256 with the webhook verification key. Fails closed
    when no key is configured — pre-fix this returned True (dev-mode
    bypass) which allowed forged events in production. Per
    SECURITY_AUDIT_2026-04-19.md HIGH-04.
    """
    verification_key = public_key or os.getenv("SENDGRID_WEBHOOK_KEY", "")
    if not verification_key:
        if _dev_allow_unsigned():
            logger.warning("sendgrid_webhook_key_not_configured_allowed_by_dev_flag")
            return True
        logger.error(
            "sendgrid_webhook_key_not_configured",
            hint="Set SENDGRID_WEBHOOK_KEY in env. HIGH-04 fail-closed.",
        )
        return False

    signed_payload = f"{timestamp}{payload.decode('utf-8')}"
    expected = hmac.new(
        verification_key.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _verify_mailchimp_signature(
    url: str,
    form: dict[str, str],
    signature: str,
) -> bool:
    """Verify Mandrill/Mailchimp webhook signature.

    Mandrill signs the webhook URL concatenated with each POST field name
    and value (in the order the fields were defined for the webhook),
    then base64-encodes HMAC-SHA1 of that with the webhook key.
    Ref: https://mailchimp.com/developer/transactional/guides/track-respond-activity-webhooks/#authenticating-webhook-requests

    Fails closed when MAILCHIMP_WEBHOOK_KEY is not set. Set
    ``AGENTICORG_WEBHOOK_ALLOW_UNSIGNED=1`` only for local dev.
    """
    verification_key = os.getenv("MAILCHIMP_WEBHOOK_KEY", "")
    if not verification_key:
        if _dev_allow_unsigned():
            logger.warning("mailchimp_webhook_key_not_configured_allowed_by_dev_flag")
            return True
        logger.error(
            "mailchimp_webhook_key_not_configured",
            hint="Set MAILCHIMP_WEBHOOK_KEY in env. HIGH-04 fail-closed.",
        )
        return False

    # Concatenate URL + sorted(key + value) to produce a stable signed string.
    # Mandrill's docs describe preserving the field order from the webhook
    # definition, but since we don't have that here we sort by key — the
    # sender must match this ordering. For strict Mandrill interop, ops
    # can override by signing in the same sorted-by-key order on their end.
    signed_parts = [url]
    for key in sorted(form.keys()):
        signed_parts.append(key)
        signed_parts.append(form[key])
    signed_data = "".join(signed_parts).encode("utf-8")
    expected = base64.b64encode(
        hmac.new(verification_key.encode("utf-8"), signed_data, hashlib.sha1).digest()
    ).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def _verify_moengage_signature(payload: bytes, signature: str) -> bool:
    """Verify MoEngage webhook signature.

    MoEngage webhooks carry an HMAC-SHA256 of the raw request body signed
    with a shared secret in the ``X-MoEngage-Signature`` header (hex).
    Fails closed when MOENGAGE_WEBHOOK_KEY is not set.
    """
    verification_key = os.getenv("MOENGAGE_WEBHOOK_KEY", "")
    if not verification_key:
        if _dev_allow_unsigned():
            logger.warning("moengage_webhook_key_not_configured_allowed_by_dev_flag")
            return True
        logger.error(
            "moengage_webhook_key_not_configured",
            hint="Set MOENGAGE_WEBHOOK_KEY in env. HIGH-04 fail-closed.",
        )
        return False
    expected = hmac.new(
        verification_key.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── SendGrid Event Webhook ─────────────────────────────────────────


@router.get("/webhooks")
async def list_webhook_endpoints():
    """List available webhook receiver endpoints."""
    return {
        "endpoints": [
            {"path": "/webhooks/email/sendgrid", "method": "POST", "description": "SendGrid inbound email webhook"},
            {"path": "/webhooks/email/mailchimp", "method": "POST", "description": "Mailchimp webhook events"},
            {"path": "/webhooks/email/moengage", "method": "POST", "description": "MoEngage campaign events"},
        ]
    }


@router.post("/webhooks/email/sendgrid")
async def sendgrid_webhook(request: Request) -> dict[str, Any]:
    """Parse SendGrid Event Webhook JSON array.

    Each event has: email, event (open/click/bounce/delivered/dropped),
    sg_message_id, timestamp, url (for clicks).
    Verified via X-Twilio-Email-Event-Webhook-Signature header.
    """
    body = await request.body()
    signature = request.headers.get("X-Twilio-Email-Event-Webhook-Signature", "")
    timestamp = request.headers.get("X-Twilio-Email-Event-Webhook-Timestamp", "")

    if not _verify_sendgrid_signature(body, signature, timestamp):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        events = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None

    if not isinstance(events, list):
        events = [events]

    processed = 0
    for event in events:
        email = event.get("email", "")
        event_type = event.get("event", "")
        sg_message_id = event.get("sg_message_id", "")
        ts = event.get("timestamp", "")
        url = event.get("url", "")

        if not email or not event_type:
            continue

        # Use sg_message_id as campaign_id proxy, or extract from categories
        campaign_id = ""
        categories = event.get("category", [])
        if isinstance(categories, list) and categories:
            campaign_id = categories[0]
        elif isinstance(categories, str):
            campaign_id = categories
        if not campaign_id:
            campaign_id = sg_message_id or "unknown"

        await _store_email_event(
            campaign_id=campaign_id,
            email=email,
            event_type=event_type,
            timestamp=ts,
            extra={"url": url} if url else None,
        )
        processed += 1

    logger.info("sendgrid_webhook_processed", count=processed)
    return {"status": "ok", "processed": processed}


# ── Mailchimp Webhook ──────────────────────────────────────────────


@router.post("/webhooks/email/mailchimp")
async def mailchimp_webhook(request: Request) -> dict[str, Any]:
    """Parse Mailchimp webhook POST form data.

    Mailchimp sends form-encoded data with: type (subscribe/unsubscribe/campaign),
    fired_at, and nested data fields.
    """
    form_data = await request.form()
    form = {str(k): str(v) for k, v in form_data.items()}
    signature = request.headers.get("X-Mandrill-Signature", "")
    webhook_url = str(request.url)
    if not _verify_mailchimp_signature(webhook_url, form, signature):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    webhook_type = form.get("type", "")
    fired_at = form.get("fired_at", "")
    data_email = form.get("data[email]", "")

    if not webhook_type:
        raise HTTPException(status_code=400, detail="Missing webhook type")

    # Map Mailchimp types to our event types
    event_map = {
        "subscribe": "subscribe",
        "unsubscribe": "unsubscribe",
        "campaign": "delivered",
        "cleaned": "bounce",
    }
    event_type = event_map.get(str(webhook_type), str(webhook_type))

    campaign_id = str(form.get("data[id]", "") or form.get("data[list_id]", "unknown"))

    if data_email:
        await _store_email_event(
            campaign_id=campaign_id,
            email=str(data_email),
            event_type=event_type,
            timestamp=str(fired_at),
        )

    logger.info("mailchimp_webhook_processed", type=webhook_type, email=data_email)
    return {"status": "ok", "type": webhook_type}


# ── MoEngage Webhook ───────────────────────────────────────────────


@router.post("/webhooks/email/moengage")
async def moengage_webhook(request: Request) -> dict[str, Any]:
    """Parse MoEngage callback JSON.

    MoEngage sends JSON with: event_type, email, campaign_id, timestamp,
    and additional metadata.
    """
    body = await request.body()
    signature = request.headers.get("X-MoEngage-Signature", "")
    if not _verify_moengage_signature(body, signature):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None

    event_type = payload.get("event_type", payload.get("type", ""))
    email = payload.get("email", payload.get("user_email", ""))
    campaign_id = payload.get("campaign_id", payload.get("campaign_name", "unknown"))
    timestamp = payload.get("timestamp", payload.get("event_time", ""))

    if not event_type or not email:
        raise HTTPException(
            status_code=400,
            detail="Missing event_type or email in payload",
        )

    # Normalize MoEngage event types
    moengage_event_map = {
        "EMAIL_OPEN": "opened",
        "EMAIL_CLICK": "clicked",
        "EMAIL_BOUNCE": "bounce",
        "EMAIL_DELIVERED": "delivered",
        "EMAIL_SENT": "sent",
        "EMAIL_DROPPED": "dropped",
    }
    normalized_event = moengage_event_map.get(event_type, event_type.lower())

    url = payload.get("url", payload.get("click_url", ""))

    await _store_email_event(
        campaign_id=campaign_id,
        email=email,
        event_type=normalized_event,
        timestamp=timestamp,
        extra={"url": url} if url else None,
    )

    logger.info(
        "moengage_webhook_processed",
        event_type=normalized_event,
        email=email,
        campaign_id=campaign_id,
    )
    return {"status": "ok", "event_type": normalized_event}
