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

from api.route_metadata import route_meta
from workflows.event_waits import WorkflowEventWaitStore

logger = structlog.get_logger()

router = APIRouter()


# SEC-2026-05-P1-007 (docs/BRUTAL_SECURITY_SCAN_2026-05-01.md):
# the unsigned-webhook bypass is a development convenience, NOT a
# production safety valve. A single misset ``AGENTICORG_WEBHOOK_ALLOW_UNSIGNED=1``
# in staging or production would silently turn every signed webhook
# into an unauthenticated ingestion endpoint — the exact operational
# mistake enterprise gates exist to prevent.
#
# Fail application startup hard if the bypass is enabled in any
# non-local environment. The check runs at module import (not per-
# request) so the misconfiguration is caught at process start, not
# after the first attacker probe.

_LOCAL_DEV_ENVS: frozenset[str] = frozenset({"local", "dev", "development", "test", "testing"})


def _enforce_unsigned_bypass_env_guard() -> None:
    """Refuse to import in staging/production when the dev bypass is set.

    Raises RuntimeError at module-import time so the API process fails
    to start rather than silently accepting unsigned webhooks. Pinned
    by tests/regression/test_security_pr_a_pins.py.
    """
    if os.getenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED") != "1":
        return
    env = (os.getenv("AGENTICORG_ENV") or "").strip().lower()
    if env in _LOCAL_DEV_ENVS:
        logger.warning(
            "webhook_unsigned_bypass_enabled_local",
            env=env,
            note="AGENTICORG_WEBHOOK_ALLOW_UNSIGNED=1 — DEV ONLY",
        )
        return
    raise RuntimeError(
        "Refusing to start: AGENTICORG_WEBHOOK_ALLOW_UNSIGNED=1 is set "
        f"in AGENTICORG_ENV={env!r}, but the unsigned webhook bypass is "
        "ONLY allowed in local/dev/test environments. Either unset the "
        "flag or set AGENTICORG_ENV=local for local debugging. "
        "(SEC-2026-05-P1-007)"
    )


_enforce_unsigned_bypass_env_guard()


def _dev_allow_unsigned() -> bool:
    """Bypass is only honored when both the flag AND the env-guard pass.

    The guard above raises if env is non-local; here we re-check at
    request time so a runtime ``os.environ`` mutation by a misbehaving
    plugin can't reactivate the bypass mid-process in production.
    """
    if os.getenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED") != "1":
        return False
    env = (os.getenv("AGENTICORG_ENV") or "").strip().lower()
    return env in _LOCAL_DEV_ENVS


def _get_redis():
    """Return an async Redis client."""
    import os

    import redis.asyncio as aioredis

    url = os.getenv("AGENTICORG_REDIS_URL", "redis://localhost:6379/1")
    return aioredis.from_url(url, decode_responses=True)


def _workflow_event_wait_store() -> WorkflowEventWaitStore:
    return WorkflowEventWaitStore()


def _email_event_type_candidates(event_type: str) -> tuple[str, ...]:
    aliases = {
        "open": ("email.open", "email.opened"),
        "opened": ("email.opened", "email.open"),
        "click": ("email.click", "email.clicked"),
        "clicked": ("email.clicked", "email.click"),
        "bounce": ("email.bounce", "email.bounced"),
        "bounced": ("email.bounced", "email.bounce"),
    }
    candidates = aliases.get(event_type.lower(), (f"email.{event_type}",))
    return tuple(dict.fromkeys(candidates))


def _extract_event_tenant_id(event_fields: dict[str, Any]) -> str | None:
    for key in ("tenant_id", "agenticorg:tenant_id", "agenticorg_tenant_id"):
        value = event_fields.get(key)
        if value:
            return str(value)
    return None


def _event_delivery_id(event_fields: dict[str, Any]) -> str:
    payload = json.dumps(event_fields, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _listener_match_criteria(listener_payload: dict[str, Any]) -> dict[str, Any]:
    raw = listener_payload.get("match", listener_payload.get("match_criteria", {}))
    return raw if isinstance(raw, dict) else {}


def _sendgrid_category_metadata(categories: Any) -> tuple[str, dict[str, str]]:
    category_list = categories if isinstance(categories, list) else [categories]
    campaign_id = ""
    extra: dict[str, str] = {}
    for raw_category in category_list:
        if not raw_category:
            continue
        category = str(raw_category)
        if ":" in category:
            key, value = category.split(":", 1)
            normalized = key.strip().lower().replace("-", "_")
            if normalized in {"tenant", "tenant_id", "agenticorg_tenant_id"}:
                extra["tenant_id"] = value.strip()
                continue
        if not campaign_id:
            campaign_id = category
    return campaign_id, extra


def _event_matches_criteria(
    event_fields: dict[str, Any],
    match_criteria: dict[str, Any],
) -> bool:
    """Return True iff every key in ``match_criteria`` is present and
    equal (case-insensitive string compare) on ``event_fields``.

    Codex 2026-04-23 re-verification blocker B: the listener stores
    match criteria (e.g. ``{"campaign_id": "abc", "email": "u@x.com"}``)
    but the webhook resume path was firing on any event of the
    matching type — so every recipient who clicked triggered every
    campaign's wait step. This helper centralises the equality check;
    tests pin the expected boolean surface.

    An empty ``match_criteria`` matches every event (back-compat: some
    older workflows are declared without a match and should still resume
    on the first event of the right type).
    """
    match_criteria = _listener_match_criteria({"match": match_criteria})
    if not match_criteria:
        return True
    for k, expected in match_criteria.items():
        actual = event_fields.get(k)
        if actual is None:
            return False
        if str(actual).strip().lower() != str(expected).strip().lower():
            return False
    return True


async def _store_email_event(
    campaign_id: str,
    email: str,
    event_type: str,
    timestamp: str | int,
    extra: dict[str, Any] | None = None,
) -> None:
    """Store the email event and check durable workflow event waits.

    Codex 2026-04-23 re-verification blocker B: resume now honours
    the ``match`` criteria the listener stored — a click event on
    campaign A must not resume a workflow that was waiting for a
    click on campaign B.
    """
    event_fields: dict[str, Any] = {
        "campaign_id": campaign_id,
        "email": email,
        "event_type": event_type,
        "timestamp": timestamp,
        **(extra or {}),
    }
    event_types = _email_event_type_candidates(event_type)
    event_fields["workflow_event_types"] = list(event_types)
    tenant_id = _extract_event_tenant_id(event_fields)
    delivery_id = _event_delivery_id(event_fields)

    store = _workflow_event_wait_store()
    await store.init()
    try:
        matched_waits = await store.claim_matching_waits(
            event_types=event_types,
            event_fields=event_fields,
            tenant_id=tenant_id,
            matched_event_id=delivery_id,
        )
    finally:
        await store.close()

    from core.tasks.workflow_tasks import _resume_workflow_wait_async, resume_workflow_wait

    for wait in matched_waits:
        logger.info(
            "workflow_event_match",
            event_type=event_type,
            email=email,
            run_id=wait.engine_run_id,
            step_id=wait.step_id,
        )
        try:
            resume_workflow_wait.delay(wait.engine_run_id, wait.step_id)
        # enterprise-gate: broad-except-ok reason=celery-enqueue-fallback-resumes-durable-wait-directly
        except Exception as exc:  # noqa: BLE001 - fall back to direct durable resume.
            logger.warning(
                "workflow_event_resume_enqueue_failed",
                run_id=wait.engine_run_id,
                step_id=wait.step_id,
                error=str(exc),
            )
            await _resume_workflow_wait_async(wait.engine_run_id, wait.step_id)

    r = None
    try:
        r = _get_redis()
        key = f"email_events:{campaign_id}:{email}"
        mapping: dict[str, str] = {
            event_type: "true",
            f"{event_type}_timestamp": str(timestamp),
        }
        if extra:
            for k, v in extra.items():
                mapping[k] = str(v)
        await r.hset(key, mapping=mapping)
    # enterprise-gate: broad-except-ok reason=email-event-history-cache-is-best-effort-after-durable-match
    except Exception as exc:  # noqa: BLE001 - Redis event history is best-effort.
        logger.warning(
            "email_event_redis_store_failed",
            campaign_id=campaign_id,
            email=email,
            event_type=event_type,
            error=str(exc),
        )
    finally:
        if r is not None:
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
@route_meta(
    auth_required=True,
    tenant_required=False,
    scope="webhooks.discovery.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="webhooks.discovery.list",
)
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
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:webhooks.email.sendgrid",
    rate_limit="provider-webhook",
    idempotency="provider-delivery-event-hash",
    audit_event="webhooks.email.sendgrid.received",
    public_reason="provider-hmac-signature-required",
)
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

        # Use sg_message_id as campaign_id proxy, or extract from categories.
        categories = event.get("category", [])
        campaign_id, category_extra = _sendgrid_category_metadata(categories)
        if not campaign_id:
            campaign_id = sg_message_id or "unknown"
        extra: dict[str, Any] = dict(category_extra)
        custom_args = event.get("custom_args")
        if isinstance(custom_args, dict):
            tenant_id = custom_args.get("tenant_id") or custom_args.get("agenticorg:tenant_id")
            if tenant_id:
                extra["tenant_id"] = tenant_id
        explicit_tenant_id = event.get("tenant_id") or event.get("agenticorg:tenant_id")
        if explicit_tenant_id:
            extra["tenant_id"] = explicit_tenant_id
        if url:
            extra["url"] = url

        await _store_email_event(
            campaign_id=campaign_id,
            email=email,
            event_type=event_type,
            timestamp=ts,
            extra=extra or None,
        )
        processed += 1

    logger.info("sendgrid_webhook_processed", count=processed)
    return {"status": "ok", "processed": processed}


# ── Mailchimp Webhook ──────────────────────────────────────────────


@router.post("/webhooks/email/mailchimp")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:webhooks.email.mailchimp",
    rate_limit="provider-webhook",
    idempotency="provider-delivery-event-hash",
    audit_event="webhooks.email.mailchimp.received",
    public_reason="provider-hmac-signature-required",
)
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
        extra: dict[str, Any] = {}
        tenant_id = (
            form.get("tenant_id")
            or form.get("agenticorg:tenant_id")
            or form.get("data[tenant_id]")
        )
        if tenant_id:
            extra["tenant_id"] = tenant_id
        await _store_email_event(
            campaign_id=campaign_id,
            email=str(data_email),
            event_type=event_type,
            timestamp=str(fired_at),
            extra=extra or None,
        )

    logger.info("mailchimp_webhook_processed", type=webhook_type, email=data_email)
    return {"status": "ok", "type": webhook_type}


# ── MoEngage Webhook ───────────────────────────────────────────────


@router.post("/webhooks/email/moengage")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:webhooks.email.moengage",
    rate_limit="provider-webhook",
    idempotency="provider-delivery-event-hash",
    audit_event="webhooks.email.moengage.received",
    public_reason="provider-hmac-signature-required",
)
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
    extra: dict[str, Any] = {}
    tenant_id = payload.get("tenant_id") or payload.get("agenticorg:tenant_id")
    if tenant_id:
        extra["tenant_id"] = tenant_id
    if url:
        extra["url"] = url

    await _store_email_event(
        campaign_id=campaign_id,
        email=email,
        event_type=normalized_event,
        timestamp=timestamp,
        extra=extra or None,
    )

    logger.info(
        "moengage_webhook_processed",
        event_type=normalized_event,
        email=email,
        campaign_id=campaign_id,
    )
    return {"status": "ok", "event_type": normalized_event}
