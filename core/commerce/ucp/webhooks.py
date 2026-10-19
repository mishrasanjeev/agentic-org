# SPDX-License-Identifier: Apache-2.0
"""Order webhooks: subscriptions, the delivery outbox and the signed dispatcher.

Subscriptions (``commerce_order_webhook_subscriptions``):

* ``platform_profile`` + ``ucp``: the platform's ``dev.ucp.shopping.order``
  ``config.webhook_url``, registered when a checkout it created completes. It
  receives that platform's orders only.
* ``merchant`` + ``ucp`` or ``acp_hmac``: endpoints the merchant manages
  through the authenticated runtime API; they receive all the merchant's
  orders.

Delivery (UCP order capability, Events): each order revision is one outbox
row per subscription, written in the transaction that changed the order. The
body is the full order entity, never a delta. ``ucp`` deliveries carry
``Webhook-Id`` and ``Webhook-Timestamp`` (both stable across retries of the
same revision: they identify the event), ``UCP-Agent: profile="<business profile>"``,
``Content-Digest`` and an RFC 9421 ``Signature`` over ``@method @authority
@path content-digest content-type ucp-agent``, signed with the merchant key
through the injected signer; with no signer or no merchant key nothing is
sent (webhooks MUST be signed) and the row retries until it dead-letters.
``acp_hmac`` deliveries use the ``core.cases.push`` scheme (HMAC-SHA256 over
``"<event id>.<timestamp>." + body`` with every key of the subscription); the
payload renderer for that scheme is pluggable (the ACP order event mapper
replaces the default).

The dispatcher follows ``core.cases.push``: lease due rows ``FOR UPDATE SKIP
LOCKED``, post with no redirects through the DNS-pinned transport, 2xx
delivered, 408/425/429/5xx and transport errors retried with exponential
backoff, other 4xx or too many attempts dead-lettered. A Celery task is
kicked after commit; the periodic sweep covers missed kicks and retries.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select

from core.cases.push import PushSettings, SigningKey, decrypt_keys, retry_delay, sign, validate_endpoint_url
from core.commerce.ledger import commerce_payload_hash
from core.commerce.ucp import releases as v
from core.commerce.ucp.httpsig import MerchantSigner, SigningKeyUnavailableError, sign_request
from core.commerce.ucp.orders import OrderEvent
from core.commerce.ucp.renderers import render_order

logger = structlog.get_logger()

SCHEMES = ("ucp", "acp_hmac")
WEBHOOK_NAMESPACE = uuid.UUID("7b4f2c58-1f0e-4d7e-9a36-5e2c1d9b8a41")
RETRYABLE_STATUS = frozenset({408, 425, 429})


class WebhookSubscriptionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- signer and payload renderers -------------------------------------------------------------

_signer: MerchantSigner | None = None
PayloadRenderer = Callable[[OrderEvent], dict[str, Any]]


def set_merchant_signer(signer: MerchantSigner | None) -> None:
    """Install the merchant key signer (the merchant key store wires this at start-up)."""
    global _signer
    _signer = signer


def merchant_signer() -> MerchantSigner | None:
    return _signer


def ucp_order_body(event: OrderEvent) -> dict[str, Any]:
    return render_order(
        snapshot=event.snapshot,
        version=event.ucp_version,
        capabilities={v.ORDER: [{"version": event.ucp_version}]},
    )


def _default_acp_payload(event: OrderEvent) -> dict[str, Any]:
    return {"type": event.event_type, "order": ucp_order_body(event)}


_renderers: dict[str, PayloadRenderer] = {"ucp": ucp_order_body, "acp_hmac": _default_acp_payload}


def register_payload_renderer(scheme: str, renderer: PayloadRenderer) -> None:
    """Replace how one scheme's payload is drawn (the ACP mapper renders ACP ``order.*`` events)."""
    if scheme not in SCHEMES:
        raise ValueError(f"unknown scheme {scheme}")
    _renderers[scheme] = renderer


# --- subscriptions ------------------------------------------------------------------------------


def _validated_url(url: str) -> str:
    from core.cases.states import CaseError

    try:
        return validate_endpoint_url(url)
    except CaseError as exc:
        raise WebhookSubscriptionError("webhook_url_invalid", exc.detail or exc.reason) from exc


def _new_key(now: datetime) -> SigningKey:
    return SigningKey(key_id=f"k_{secrets.token_hex(8)}", secret=secrets.token_urlsafe(32), created_at=now.isoformat())


async def _encrypt_keys(tenant_id: uuid.UUID, keys: list[SigningKey]) -> dict[str, str]:
    from core.crypto.tenant_secrets import encrypt_with_kek, resolve_tenant_kek

    plaintext = json.dumps([{"key_id": k.key_id, "secret": k.secret, "created_at": k.created_at} for k in keys])
    kek = await resolve_tenant_kek(tenant_id)
    return {"_encrypted": await asyncio.to_thread(encrypt_with_kek, plaintext, kek)}


def subscription_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "merchant_id": row.merchant_id,
        "url": row.url,
        "scheme": row.scheme,
        "source": row.source,
        "platform_profile_url": row.platform_profile_url,
        "enabled": row.enabled,
        "active_key_id": row.active_key_id,
    }


async def create_subscription(
    session: Any, *, tenant_id: uuid.UUID, merchant_id: str, url: str, scheme: str, now: datetime
) -> tuple[Any, SigningKey | None]:
    """A merchant-managed subscription; an ``acp_hmac`` one gets a fresh key (returned once, stored encrypted)."""
    from core.models.commerce_ucp import CommerceOrderWebhookSubscriptionRow

    if scheme not in SCHEMES:
        raise WebhookSubscriptionError("scheme_invalid", "scheme must be ucp or acp_hmac")
    target = _validated_url(url)
    existing = (
        await session.execute(
            select(CommerceOrderWebhookSubscriptionRow).where(
                CommerceOrderWebhookSubscriptionRow.tenant_id == tenant_id,
                CommerceOrderWebhookSubscriptionRow.merchant_id == merchant_id,
                CommerceOrderWebhookSubscriptionRow.url == target,
                CommerceOrderWebhookSubscriptionRow.scheme == scheme,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise WebhookSubscriptionError("subscription_exists", "This endpoint is already subscribed.")
    key = _new_key(now) if scheme == "acp_hmac" else None
    row = CommerceOrderWebhookSubscriptionRow(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        url=target,
        scheme=scheme,
        source="merchant",
        platform_profile_url=None,
        enabled=True,
        signing_keys_encrypted=await _encrypt_keys(tenant_id, [key]) if key else {},
        active_key_id=key.key_id if key else None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    return row, key


async def rotate_subscription_key(session: Any, row: Any, *, now: datetime) -> SigningKey:
    """Add a new active key and keep the previous one, so receivers can switch before it is retired."""
    if row.scheme != "acp_hmac":
        raise WebhookSubscriptionError("scheme_invalid", "Only acp_hmac subscriptions have shared keys.")
    keys = await decrypt_keys(row)
    key = _new_key(now)
    keys = [key, *keys][:2]
    row.signing_keys_encrypted = await _encrypt_keys(row.tenant_id, keys)
    row.active_key_id = key.key_id
    row.updated_at = now
    return key


async def ensure_platform_subscription(
    session: Any, *, tenant_id: uuid.UUID, merchant_id: str, webhook_url: str, platform_profile_url: str, now: datetime
) -> Any | None:
    """The platform's own subscription (from its profile); None when its URL is not deliverable."""
    from core.models.commerce_ucp import CommerceOrderWebhookSubscriptionRow

    try:
        target = _validated_url(webhook_url)
    except WebhookSubscriptionError:
        logger.warning("ucp_platform_webhook_url_rejected")
        return None
    row = (
        await session.execute(
            select(CommerceOrderWebhookSubscriptionRow).where(
                CommerceOrderWebhookSubscriptionRow.tenant_id == tenant_id,
                CommerceOrderWebhookSubscriptionRow.merchant_id == merchant_id,
                CommerceOrderWebhookSubscriptionRow.url == target,
                CommerceOrderWebhookSubscriptionRow.scheme == "ucp",
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = CommerceOrderWebhookSubscriptionRow(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        url=target,
        scheme="ucp",
        source="platform_profile",
        platform_profile_url=platform_profile_url,
        enabled=True,
        signing_keys_encrypted={},
        active_key_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return row


# --- outbox -------------------------------------------------------------------------------------


def webhook_id_for(subscription_id: uuid.UUID, order_id: str, revision: int) -> uuid.UUID:
    """Stable per subscription and order revision, so every retry carries the same ``Webhook-Id``."""
    return uuid.uuid5(WEBHOOK_NAMESPACE, f"{subscription_id}:{order_id}:{revision}")


async def enqueue_order_deliveries(
    session: Any, event: OrderEvent, *, platform_profile_url: str | None, now: datetime
) -> int:
    """One outbox row per enabled subscription that should see this order revision."""
    from sqlalchemy.dialects.postgresql import insert

    from core.models.commerce_ucp import CommerceOrderWebhookDeliveryRow, CommerceOrderWebhookSubscriptionRow

    tenant = uuid.UUID(str(event.tenant_id))
    rows = (
        await session.scalars(
            select(CommerceOrderWebhookSubscriptionRow).where(
                CommerceOrderWebhookSubscriptionRow.tenant_id == tenant,
                CommerceOrderWebhookSubscriptionRow.merchant_id == event.merchant_id,
                CommerceOrderWebhookSubscriptionRow.enabled.is_(True),
            )
        )
    ).all()
    count = 0
    for sub in rows:
        if sub.source == "platform_profile" and sub.platform_profile_url != platform_profile_url:
            continue
        payload = _renderers[sub.scheme](event)
        await session.execute(
            insert(CommerceOrderWebhookDeliveryRow)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant,
                subscription_id=sub.id,
                merchant_id=event.merchant_id,
                order_id=event.order_id,
                order_revision=event.revision,
                webhook_id=webhook_id_for(sub.id, event.order_id, event.revision),
                event_type=event.event_type,
                payload=payload,
                payload_sha256=commerce_payload_hash(payload),
                status="pending",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "subscription_id", "order_id", "order_revision"])
        )
        count += 1
    return count


# --- dispatcher ---------------------------------------------------------------------------------


def _default_session_factory(tenant_id: uuid.UUID) -> AbstractAsyncContextManager[Any]:
    from core.database import get_tenant_session

    return get_tenant_session(tenant_id)


def _default_transport() -> httpx.AsyncBaseTransport:
    from core.security.egress import build_pinned_async_transport, egress_dns_validation_required

    return build_pinned_async_transport(require_dns=egress_dns_validation_required())


async def _tenants_with_pending() -> list[uuid.UUID]:
    from sqlalchemy import text

    from core.database import async_session_factory
    from core.models.tenant import Tenant

    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        return list((await session.scalars(select(Tenant.id))).all())


@dataclass
class DispatchReport:
    delivered: int = 0
    retried: int = 0
    dead_lettered: int = 0


@dataclass
class OrderWebhookDispatcher:
    session_factory: Callable[[uuid.UUID], AbstractAsyncContextManager[Any]] = _default_session_factory
    transport_factory: Callable[[], httpx.AsyncBaseTransport] = _default_transport
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    settings: PushSettings = field(default_factory=PushSettings)
    signer: Callable[[], MerchantSigner | None] = merchant_signer
    tenant_ids: Callable[[], Awaitable[list[uuid.UUID]]] = _tenants_with_pending

    async def dispatch_all(self) -> DispatchReport:
        total = DispatchReport()
        for tenant_id in await self.tenant_ids():
            report = await self.dispatch_tenant(tenant_id)
            total.delivered += report.delivered
            total.retried += report.retried
            total.dead_lettered += report.dead_lettered
        return total

    async def dispatch_tenant(self, tenant_id: uuid.UUID) -> DispatchReport:
        from core.models.commerce_ucp import (
            CommerceOrderRow,
            CommerceOrderWebhookDeliveryRow,
            CommerceOrderWebhookSubscriptionRow,
        )

        report = DispatchReport()
        now = self.clock()
        leased: list[dict[str, Any]] = []
        async with self.session_factory(tenant_id) as session:
            rows = list(
                (
                    await session.execute(
                        select(CommerceOrderWebhookDeliveryRow)
                        .where(
                            CommerceOrderWebhookDeliveryRow.tenant_id == tenant_id,
                            CommerceOrderWebhookDeliveryRow.status == "pending",
                            CommerceOrderWebhookDeliveryRow.next_attempt_at <= now,
                        )
                        .order_by(CommerceOrderWebhookDeliveryRow.next_attempt_at)
                        .limit(self.settings.batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            for row in rows:
                sub = await session.get(CommerceOrderWebhookSubscriptionRow, row.subscription_id)
                order = (
                    await session.execute(
                        select(CommerceOrderRow).where(
                            CommerceOrderRow.tenant_id == tenant_id, CommerceOrderRow.id == row.order_id
                        )
                    )
                ).scalar_one_or_none()
                row.attempts = row.attempts + 1
                row.next_attempt_at = now + timedelta(seconds=self.settings.lease_seconds)
                row.updated_at = now
                if sub is None or not sub.enabled or order is None:
                    row.status, row.dead_lettered_at, row.last_error = "dead_lettered", now, "subscription_disabled"
                    report.dead_lettered += 1
                    continue
                keys = await decrypt_keys(sub) if sub.scheme == "acp_hmac" else []
                keys.sort(key=lambda k: k.key_id != sub.active_key_id)
                leased.append(
                    {
                        "id": row.id,
                        "webhook_id": str(row.webhook_id),
                        "event_type": row.event_type,
                        "payload": dict(row.payload),
                        "attempts": row.attempts,
                        "url": sub.url,
                        "scheme": sub.scheme,
                        "keys": keys,
                        "merchant_id": row.merchant_id,
                        "business_profile": f"https://{order.serving_host}/.well-known/ucp",
                        # The event time: every retry of one revision repeats it with the Webhook-Id.
                        "event_timestamp": int(row.created_at.timestamp()),
                    }
                )
        if not leased:
            return report
        results = []
        async with httpx.AsyncClient(
            transport=self.transport_factory(), timeout=self.settings.timeout_seconds, follow_redirects=False
        ) as client:
            for item in leased:
                results.append((item, *(await self._send(client, item, tenant_id))))
        finished = self.clock()
        async with self.session_factory(tenant_id) as session:
            for item, error, status_code in results:
                row = await session.get(CommerceOrderWebhookDeliveryRow, item["id"])
                if row is None:
                    continue
                row.last_status_code = status_code
                row.updated_at = finished
                if not error:
                    row.status, row.delivered_at, row.last_error = "delivered", finished, None
                    report.delivered += 1
                    continue
                row.last_error = error[:64]
                retryable = status_code is None or status_code in RETRYABLE_STATUS or status_code >= 500
                if retryable and item["attempts"] < self.settings.max_attempts:
                    row.next_attempt_at = finished + timedelta(
                        seconds=retry_delay(item["attempts"], uuid.UUID(item["webhook_id"]), self.settings)
                    )
                    report.retried += 1
                else:
                    reason = "max_attempts_exceeded" if retryable else "endpoint_rejected"
                    row.status, row.dead_lettered_at, row.last_error = (
                        "dead_lettered",
                        finished,
                        f"{reason}:{error}"[:64],
                    )
                    report.dead_lettered += 1
                    logger.warning("ucp_order_webhook_dead_lettered", delivery_id=str(item["id"]), reason=reason)
        return report

    async def _send(
        self, client: httpx.AsyncClient, item: Mapping[str, Any], tenant_id: uuid.UUID
    ) -> tuple[str, int | None]:
        body = json.dumps(item["payload"], separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        timestamp = int(self.clock().timestamp())
        if item["scheme"] == "acp_hmac":
            # The HMAC scheme signs the attempt time (receivers enforce a tolerance window).
            if not item["keys"]:
                return "signing_key_unavailable", None
            headers = sign(
                body, event_id=item["webhook_id"], event_type=item["event_type"], timestamp=timestamp, keys=item["keys"]
            )
        else:
            signer = self.signer()
            if signer is None:
                return "signing_key_unavailable", None
            base_headers = {
                "Content-Type": "application/json",
                "Webhook-Id": item["webhook_id"],
                "Webhook-Timestamp": str(item["event_timestamp"]),
                "UCP-Agent": f'profile="{item["business_profile"]}"',
            }
            try:
                headers = await sign_request(
                    method="POST",
                    url=item["url"],
                    headers=base_headers,
                    body=body,
                    signer=signer,
                    tenant_id=str(tenant_id),
                    merchant_id=item["merchant_id"],
                    created=timestamp,
                )
            except SigningKeyUnavailableError:
                return "signing_key_unavailable", None
        try:
            response = await client.post(item["url"], content=body, headers=headers)
        except httpx.TimeoutException:
            return "timeout", None
        except httpx.HTTPError as exc:
            return f"transport_{type(exc).__name__}", None
        if 200 <= response.status_code < 300:
            return "", response.status_code
        return f"http_{response.status_code}", response.status_code


def _send_kick(tenant_id: uuid.UUID) -> None:
    try:
        from core.tasks.ucp_order_webhook_tasks import dispatch_order_webhooks

        dispatch_order_webhooks.delay(str(tenant_id))
    # enterprise-gate: broad-except-ok reason=broker-failure-falls-back-to-the-periodic-sweep-durable-outbox
    except Exception as exc:
        logger.warning("ucp_order_webhook_kick_failed", error=type(exc).__name__)


def kick_dispatch(tenant_id: uuid.UUID) -> None:
    """Ask a worker to deliver now (off the event loop); the sweep delivers anyway if the broker is down."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _send_kick(tenant_id)
        return
    loop.run_in_executor(None, _send_kick, tenant_id)
