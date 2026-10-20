# SPDX-License-Identifier: Apache-2.0
"""UCP REST binding: checkout sessions, carts and orders at ``https://<merchant serving host>/ucp``.

Mounted at ``/ucp`` (no ``/api/v1`` prefix): the UCP profile advertises
``https://<host>/ucp`` as the ``dev.ucp.shopping`` REST endpoint, and the
merchant is resolved from the ``Host`` header inside the tenant of the
credential the merchant issued to the platform (``Authorization: Bearer``
with a tenant API key holding ``ucp:read``/``ucp:write``). An unknown host is
404. Behind ``AGENTICORG_UCP_CHECKOUT_ENABLED`` (default off): every route
answers 404 while it is off.

Operations (UCP 2026-04-08 and 2026-08-25 REST binding): ``POST
/checkout-sessions`` (201), ``GET``/``PUT /checkout-sessions/{id}``, ``POST
/checkout-sessions/{id}/complete``, ``POST /checkout-sessions/{id}/cancel``,
``POST /carts`` (201), ``GET``/``PUT /carts/{id}``, ``POST
/carts/{id}/cancel`` and ``GET /orders/{id}``. The logic lives in
``core.commerce.ucp.handlers``; this module is the HTTP adapter. Every write
records its ledger event; an answer with no business write (a protocol
refusal, an idempotent replay) is recorded here in its own transaction.

The merchant-side order webhook subscription and order event endpoints are
under ``/api/v1/commerce/runtime/ucp`` (tenant administrators).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.commerce.c6z_runtime_vertical import ShopifyCredentials
from core.commerce.ledger import commerce_payload_hash, record_commerce_event, record_commerce_event_in_own_transaction
from core.commerce.ucp import handlers
from core.commerce.ucp.capabilities import publish_to_profile_registry
from core.commerce.ucp.items import ProductEvidenceCatalog
from core.commerce.ucp.merchant import MerchantUcp, PublishedHostResolver
from core.commerce.ucp.shopify_reads import ShopifyAdminReads, ShopifyReads
from core.commerce.ucp.webhooks import (
    WebhookSubscriptionError,
    create_subscription,
    kick_dispatch,
    merchant_signer,
    rotate_subscription_key,
    subscription_view,
)
from core.config import settings
from core.database import get_tenant_session

logger = structlog.get_logger()

# The transaction capabilities and payment handlers appear in /.well-known/ucp while
# AGENTICORG_UCP_CHECKOUT_ENABLED is on (served predicates read the flag per render).
publish_to_profile_registry()


def require_ucp_checkout_enabled() -> None:
    if not settings.ucp_checkout_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(prefix="/ucp", tags=["UCP"], dependencies=[Depends(require_ucp_checkout_enabled)])
runtime_router = APIRouter(
    prefix="/commerce/runtime/ucp",
    tags=["Commerce Runtime"],
    dependencies=[require_tenant_admin],
)


# --- runtime wiring ---------------------------------------------------------------------------


async def _runtime_scope(merchant: MerchantUcp) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
    from api.v1 import commerce_runtime

    return await commerce_runtime._load_runtime_scope(
        tenant_id=merchant.tenant_id,
        merchant_id=merchant.merchant_id,
        seller_agent_id=merchant.seller_agent_id,
        buyer_agent_id=None,
    )


async def _products(merchant: MerchantUcp) -> Sequence[dict[str, Any]]:
    _records, products, _capabilities = await _runtime_scope(merchant)
    return products


async def _arbiter_scope(merchant: MerchantUcp) -> tuple[Sequence[Any], dict[str, Any] | None]:
    from api.v1 import commerce_runtime

    records, _products_unused, _capabilities = await _runtime_scope(merchant)
    config = await commerce_runtime._merchant_config_for_arbiter(
        merchant.tenant_id, merchant.merchant_id, merchant.seller_agent_id
    )
    return records, config


async def _shopify_credentials(merchant: MerchantUcp) -> ShopifyCredentials | None:
    """The merchant's Shopify connector credentials (tenant vault), or None when not configured."""
    from api.v1 import commerce_runtime
    from core.crypto.tenant_secrets import decrypt_for_tenant

    tenant = uuid.UUID(merchant.tenant_id)
    async with get_tenant_session(tenant) as session:
        row = await commerce_runtime._load_shopify_connector_config_row(session, tenant, merchant.merchant_id)
    stored = (row.credentials_encrypted or {}).get("_encrypted") if row is not None else None
    if not isinstance(stored, str) or not stored:
        return None
    try:
        data = json.loads(await asyncio.to_thread(decrypt_for_tenant, stored))
    # enterprise-gate: broad-except-ok reason=unreadable-shopify-vault-fails-closed-to-no-shopify-reads
    except Exception:
        logger.warning("ucp_shopify_credentials_unreadable")
        return None
    token = str(data.get("admin_access_token") or data.get("access_token") or "").strip()
    domain = str(data.get("shop_domain") or (row.config or {}).get("shop_domain") or "").strip().lower()
    if not token or not domain.endswith(".myshopify.com"):
        return None
    version = str(data.get("api_version") or (row.config or {}).get("api_version") or "2026-04")
    return ShopifyCredentials(shop_domain=domain, admin_access_token=token, api_version=version)


async def _shopify_reads(merchant: MerchantUcp) -> ShopifyReads | None:
    credentials = await _shopify_credentials(merchant)
    if credentials is None:
        return None
    return ShopifyAdminReads(credentials, cache_key=(merchant.tenant_id, merchant.merchant_id))


def _rates_for(merchant: MerchantUcp) -> Any:
    async def _rates(m: MerchantUcp) -> list[Any]:
        reads = await _shopify_reads(m)
        if reads is None:
            return []
        try:
            return list(await reads.delivery_rates())
        # enterprise-gate: broad-except-ok reason=rate-lookup-failure-degrades-to-no-shipping-option-business-message
        except Exception as exc:
            logger.warning("ucp_delivery_rates_unavailable", error_type=type(exc).__name__)
            return []

    return _rates


def _published_capabilities(merchant: MerchantUcp, version: str) -> set[str]:
    """What the merchant's profile advertises for ``version`` (the profile registry decides, no I/O)."""
    from core.commerce.ucp.registry import UcpServeContext, ucp_registry

    ctx = UcpServeContext(
        host=merchant.serving_host,
        version=version,
        tenant_id=merchant.tenant_id,
        merchant_id=merchant.merchant_id,
        seller_agent_id=merchant.seller_agent_id,
        namespace=merchant.namespace,
        location_count=len(merchant.pickup_locations),
        merchant_config=merchant.raw_config,
    )
    return ucp_registry.served_capability_names(ctx)


async def _policies(merchant: MerchantUcp) -> list[dict[str, Any]]:
    """``policies[]`` from the policy snapshot taken at verified caching, while its artifact stays usable."""
    from core.commerce.ucp.catalog import usable_artifact_ids
    from core.commerce.ucp.policies import map_oacp_policy_to_ucp_policies
    from core.commerce.ucp.state import ucp_state
    from core.commerce.ucp.store import load_shared_cache_records

    snapshot = ucp_state(merchant.raw_config.get("public_publishing")).get("verified_policy")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("policy"), dict):
        return []
    records = await load_shared_cache_records(
        tenant_id=merchant.tenant_id, merchant_id=merchant.merchant_id, seller_agent_id=merchant.seller_agent_id
    )
    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if snapshot.get("artifact_id") not in usable_artifact_ids(
        cache_records=records, now_iso=now_iso, artifact_type="policy"
    ):
        return []
    return map_oacp_policy_to_ucp_policies(snapshot["policy"], vendor_namespace=merchant.namespace)


def build_runtime() -> handlers.UcpRuntime:
    """The production wiring (tests replace ``runtime_factory``)."""
    return handlers.UcpRuntime(
        resolver=PublishedHostResolver(),
        catalog_for=lambda _merchant: ProductEvidenceCatalog(_products),
        rates_for=_rates_for,
        shopify_reads_for=_shopify_reads,
        arbiter_scope=_arbiter_scope,
        signer=merchant_signer,
        after_commit=kick_dispatch,
        published=_published_capabilities,
        policies_for=_policies,
    )


runtime_factory = build_runtime


# --- REST adapter -----------------------------------------------------------------------------


async def _call(operation: str, request: Request, tenant_id: str, resource_id: str | None = None) -> JSONResponse:
    raw = await request.body()
    req = handlers.UcpRequest(
        tenant_id=tenant_id,
        host=request.headers.get("host", ""),
        ucp_agent=request.headers.get("ucp-agent"),
        raw_body=raw,
        idempotency_key=request.headers.get("idempotency-key"),
        request_id=request.headers.get("request-id"),
    )
    response = await handlers.run(operation, runtime_factory(), req, resource_id)
    event = handlers.EVENTS.get(operation)
    if event and not response.recorded:
        await record_commerce_event_in_own_transaction(
            tenant_id=tenant_id,
            event=event,
            outcome=response.outcome if response.outcome in ("success", "refused", "blocked") else "refused",
            merchant_id=response.merchant_id,
            seller_agent_id=response.seller_agent_id,
            buyer_agent_id=None,
            payload={
                "operation": operation,
                "status": response.status,
                "body_sha256": commerce_payload_hash(raw.decode("utf-8", "replace")),
            },
            resource_id=response.resource_id or operation,
            details={"http_status": response.status, "operation": operation},
        )
    headers = dict(response.headers)
    if req.request_id:
        headers["Request-Id"] = req.request_id[:128]
    return JSONResponse(status_code=response.status, content=response.body, headers=headers)


@router.post("/checkout-sessions", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ucp.checkout.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.ucp.checkout.create",
)
async def create_checkout_session(request: Request, tenant_id: str = Depends(get_current_tenant)) -> JSONResponse:
    return await _call("create_checkout", request, tenant_id)


@router.get("/checkout-sessions/{session_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ucp.checkout.read",
    rate_limit="ucp-platform",
    idempotency="read-only",
)
async def get_checkout_session(
    session_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> JSONResponse:
    return await _call("get_checkout", request, tenant_id, session_id)


@router.put("/checkout-sessions/{session_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ucp.checkout.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.ucp.checkout.update",
)
async def update_checkout_session(
    session_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> JSONResponse:
    return await _call("update_checkout", request, tenant_id, session_id)


@router.post("/checkout-sessions/{session_id}/complete")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ucp.checkout.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.ucp.checkout.complete",
)
async def complete_checkout_session(
    session_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> JSONResponse:
    return await _call("complete_checkout", request, tenant_id, session_id)


@router.post("/checkout-sessions/{session_id}/cancel")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ucp.checkout.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.ucp.checkout.cancel",
)
async def cancel_checkout_session(
    session_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> JSONResponse:
    return await _call("cancel_checkout", request, tenant_id, session_id)


@router.post("/carts", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ucp.cart.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.ucp.cart.create",
)
async def create_cart(request: Request, tenant_id: str = Depends(get_current_tenant)) -> JSONResponse:
    return await _call("create_cart", request, tenant_id)


@router.get("/carts/{cart_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ucp.cart.read",
    rate_limit="ucp-platform",
    idempotency="read-only",
)
async def get_cart(cart_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)) -> JSONResponse:
    return await _call("get_cart", request, tenant_id, cart_id)


@router.put("/carts/{cart_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ucp.cart.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.ucp.cart.update",
)
async def update_cart(cart_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)) -> JSONResponse:
    return await _call("update_cart", request, tenant_id, cart_id)


@router.post("/carts/{cart_id}/cancel")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ucp.cart.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.ucp.cart.cancel",
)
async def cancel_cart(cart_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)) -> JSONResponse:
    return await _call("cancel_cart", request, tenant_id, cart_id)


@router.get("/orders/{order_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="ucp.order.read",
    rate_limit="ucp-platform",
    idempotency="read-only",
)
async def get_order(order_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)) -> JSONResponse:
    return await _call("get_order", request, tenant_id, order_id)


# --- merchant runtime: order webhook subscriptions and order events ---------------------------


class SubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_id: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=8, max_length=2048)
    scheme: str = Field(default="ucp", pattern="^(ucp|acp_hmac)$")


class SubscriptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class OrderEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fulfillment_event: dict[str, Any] | None = None
    adjustment: dict[str, Any] | None = None


def _tenant_uuid(tenant_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(tenant_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="tenant context is not a UUID") from exc


async def _merchant_exists(session: Any, tenant_id: str, merchant_id: str) -> bool:
    from core.models.commerce_c6z_runtime import C6ZMerchantCommerceConfigRow

    found = (
        await session.execute(
            select(C6ZMerchantCommerceConfigRow.config_id).where(
                C6ZMerchantCommerceConfigRow.tenant_id == str(tenant_id),
                C6ZMerchantCommerceConfigRow.merchant_id == merchant_id,
            )
        )
    ).first()
    return found is not None


async def _subscription(session: Any, tenant: uuid.UUID, subscription_id: str) -> Any:
    from core.models.commerce_ucp import CommerceOrderWebhookSubscriptionRow as Row

    try:
        key = uuid.UUID(subscription_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Subscription not found") from exc
    row = (await session.execute(select(Row).where(Row.tenant_id == tenant, Row.id == key))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return row


@runtime_router.get("/order-webhook-subscriptions")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ucp.subscriptions.read",
    rate_limit="standard",
    idempotency="read-only",
)
async def list_order_webhook_subscriptions(
    merchant_id: str | None = None, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    from core.models.commerce_ucp import CommerceOrderWebhookSubscriptionRow as Row

    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        query = select(Row).where(Row.tenant_id == tenant)
        if merchant_id:
            query = query.where(Row.merchant_id == merchant_id)
        rows = (await session.scalars(query.order_by(Row.created_at))).all()
    return {"subscriptions": [subscription_view(row) for row in rows]}


@runtime_router.post("/order-webhook-subscriptions", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ucp.subscriptions.write",
    rate_limit="commerce-runtime-write",
    idempotency="subscription-target",
    audit_event="commerce.ucp.order_webhook_subscription.create",
)
async def create_order_webhook_subscription(
    body: SubscriptionCreate, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    tenant = _tenant_uuid(tenant_id)
    now = datetime.now(UTC)
    async with get_tenant_session(tenant) as session:
        if not await _merchant_exists(session, tenant_id, body.merchant_id):
            raise HTTPException(status_code=404, detail="Merchant config not found")
        try:
            row, key = await create_subscription(
                session, tenant_id=tenant, merchant_id=body.merchant_id, url=body.url, scheme=body.scheme, now=now
            )
        except WebhookSubscriptionError as exc:
            raise HTTPException(status_code=409 if exc.code == "subscription_exists" else 422, detail=exc.code) from exc
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event="commerce.ucp.order_webhook_subscription.create",
            outcome="success",
            merchant_id=body.merchant_id,
            seller_agent_id=None,
            buyer_agent_id=None,
            payload={"subscription_id": str(row.id), "url": row.url, "scheme": row.scheme},
            resource_id=str(row.id),
        )
        view = subscription_view(row)
    if key is not None:
        # Shown once; only its encrypted form is stored.
        view["signing_key"] = {"key_id": key.key_id, "secret": key.secret}
    return view


@runtime_router.patch("/order-webhook-subscriptions/{subscription_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ucp.subscriptions.write",
    rate_limit="commerce-runtime-write",
    idempotency="subscription-target",
    audit_event="commerce.ucp.order_webhook_subscription.update",
)
async def update_order_webhook_subscription(
    subscription_id: str, body: SubscriptionUpdate, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        row = await _subscription(session, tenant, subscription_id)
        row.enabled = body.enabled
        row.updated_at = datetime.now(UTC)
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event="commerce.ucp.order_webhook_subscription.update",
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=None,
            buyer_agent_id=None,
            payload={"subscription_id": str(row.id), "enabled": body.enabled},
            resource_id=str(row.id),
        )
        return subscription_view(row)


@runtime_router.post("/order-webhook-subscriptions/{subscription_id}/rotate-key")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ucp.subscriptions.write",
    rate_limit="commerce-runtime-write",
    idempotency="subscription-target",
    audit_event="commerce.ucp.order_webhook_subscription.rotate_key",
)
async def rotate_order_webhook_subscription_key(
    subscription_id: str, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        row = await _subscription(session, tenant, subscription_id)
        try:
            key = await rotate_subscription_key(session, row, now=datetime.now(UTC))
        except WebhookSubscriptionError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event="commerce.ucp.order_webhook_subscription.rotate_key",
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=None,
            buyer_agent_id=None,
            payload={"subscription_id": str(row.id), "active_key_id": key.key_id},
            resource_id=str(row.id),
        )
        view = subscription_view(row)
    view["signing_key"] = {"key_id": key.key_id, "secret": key.secret}
    return view


@runtime_router.get("/orders/{order_id}/webhook-deliveries")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ucp.deliveries.read",
    rate_limit="standard",
    idempotency="read-only",
)
async def list_order_webhook_deliveries(order_id: str, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    from core.models.commerce_ucp import CommerceOrderWebhookDeliveryRow as Row

    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        rows = (
            await session.scalars(
                select(Row).where(Row.tenant_id == tenant, Row.order_id == order_id).order_by(Row.created_at)
            )
        ).all()
    return {
        "deliveries": [
            {
                "id": str(r.id),
                "subscription_id": str(r.subscription_id),
                "order_revision": r.order_revision,
                "webhook_id": str(r.webhook_id),
                "event_type": r.event_type,
                "status": r.status,
                "attempts": r.attempts,
                "last_status_code": r.last_status_code,
                "last_error": r.last_error,
            }
            for r in rows
        ]
    }


@runtime_router.post("/order-webhook-deliveries/{delivery_id}/replay")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ucp.deliveries.write",
    rate_limit="commerce-runtime-write",
    idempotency="subscription-target",
    audit_event="commerce.ucp.order_webhook_delivery.replay",
)
async def replay_order_webhook_delivery(
    delivery_id: str, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    from core.models.commerce_ucp import CommerceOrderWebhookDeliveryRow as Row

    tenant = _tenant_uuid(tenant_id)
    try:
        key = uuid.UUID(delivery_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Delivery not found") from exc
    async with get_tenant_session(tenant) as session:
        row = (
            await session.execute(select(Row).where(Row.tenant_id == tenant, Row.id == key).with_for_update())
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Delivery not found")
        if row.status != "dead_lettered":
            raise HTTPException(status_code=409, detail="Only dead-lettered deliveries can be replayed")
        now = datetime.now(UTC)
        row.status, row.attempts, row.next_attempt_at = "pending", 0, now
        row.last_error, row.dead_lettered_at, row.updated_at = None, None, now
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event="commerce.ucp.order_webhook_delivery.replay",
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=None,
            buyer_agent_id=None,
            payload={"delivery_id": str(row.id), "webhook_id": str(row.webhook_id)},
            resource_id=str(row.id),
        )
    kick_dispatch(tenant)
    return {"id": delivery_id, "status": "pending"}


@runtime_router.post("/orders/{order_id}/events")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ucp.orders.write",
    rate_limit="commerce-runtime-write",
    idempotency="subscription-target",
    audit_event="commerce.ucp.order.update",
)
async def record_order_event(
    order_id: str, body: OrderEventRequest, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Record a fulfillment event or an adjustment; the full order snapshot is sent to every subscriber."""
    from core.commerce.ucp.renderers import render_order

    tenant = _tenant_uuid(tenant_id)
    try:
        revision, snapshot, version = await handlers.apply_order_change(
            tenant_id=tenant_id,
            order_id=order_id,
            fulfillment_event=body.fulfillment_event,
            adjustment=body.adjustment,
            now=datetime.now(UTC),
        )
    except handlers.OrderChangeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    kick_dispatch(tenant)
    capabilities = {"dev.ucp.shopping.order": [{"version": version}]}
    rendered = render_order(snapshot=snapshot, version=version, capabilities=capabilities)
    return {"revision": revision, "order": rendered}


# --- payments: refunds, tracking and Plural events (Phase 4) -------------------------------------


class OrderRefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = Field(ge=1, le=9_007_199_254_740_991)
    reason: str | None = Field(default=None, max_length=200)
    request_id: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


@runtime_router.post("/orders/{order_id}/refunds")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ucp.orders.write",
    rate_limit="commerce-runtime-write",
    idempotency="refund-request-id",
    audit_event="commerce.ucp.order.refund",
)
async def refund_order(
    order_id: str, body: OrderRefundRequest, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Refund part or all of a captured order: a fresh ``refund`` arbiter decision, then the Plural executor."""
    from core.commerce.order_refunds import RefundRequestError
    from core.commerce.order_refunds import refund_order as orchestrate

    tenant = _tenant_uuid(tenant_id)
    try:
        outcome = await orchestrate(
            tenant_id=str(tenant),
            order_id=order_id,
            amount_minor_units=body.amount,
            reason=body.reason,
            now=datetime.now(UTC),
            request_id=body.request_id,
        )
    except RefundRequestError as exc:
        await record_commerce_event_in_own_transaction(
            tenant_id=tenant_id,
            event="commerce.ucp.order.refund",
            outcome="blocked",
            merchant_id=None,
            seller_agent_id=None,
            buyer_agent_id=None,
            payload={"order_id": order_id, "reason": exc.code},
            resource_id=order_id,
            details={"reason": exc.code},
        )
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    if outcome.order_revision is not None:
        kick_dispatch(tenant)
    return {
        "status": outcome.status,
        "reason": outcome.reason,
        "decision": outcome.decision,
        "adjustment_id": outcome.adjustment_id,
        "order_revision": outcome.order_revision,
    }


class ShopifyFulfillmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A Shopify Admin Fulfillment or FulfillmentEvent object, as the merchant's Shopify integration received it.
    fulfillment: dict[str, Any]


@runtime_router.post("/orders/{order_id}/shopify-fulfillments")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ucp.orders.write",
    rate_limit="commerce-runtime-write",
    idempotency="source-event-id",
    audit_event="commerce.ucp.order.update",
)
async def record_shopify_fulfillment(
    order_id: str, body: ShopifyFulfillmentRequest, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Normalise a Shopify fulfillment (or fulfillment event) into a UCP order fulfillment event and record it."""
    from core.commerce.fulfillment_tracking import TrackingNormalisationError, normalise_shopify_fulfillment
    from core.commerce.ucp.renderers import render_order
    from core.models.commerce_ucp import CommerceOrderRow

    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        row = (
            await session.execute(
                select(CommerceOrderRow).where(CommerceOrderRow.tenant_id == tenant, CommerceOrderRow.id == order_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Order not found")
        snapshot = dict(row.snapshot or {})
        aliases = await _carrier_aliases(session, tenant, row)

    async def _record_outcome(outcome: str, reason: str) -> None:
        await record_commerce_event_in_own_transaction(
            tenant_id=tenant_id,
            event="commerce.ucp.order.update",
            outcome=outcome,
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=None,
            payload={"order_id": order_id, "reason": reason},
            resource_id=order_id,
            details={"reason": reason, "source": "shopify_fulfillment"},
        )

    try:
        normalised = normalise_shopify_fulfillment(body.fulfillment, snapshot, carrier_aliases=aliases)
    except TrackingNormalisationError as exc:
        await _record_outcome("refused", "tracking_unmappable")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if normalised is None:
        await _record_outcome("blocked", "no_tracking_status")
        return {"recorded": False, "reason": "no_tracking_status"}
    try:
        revision, updated, version = await handlers.apply_order_change(
            tenant_id=tenant_id,
            order_id=order_id,
            fulfillment_event=normalised.event,
            adjustment=None,
            now=datetime.now(UTC),
            event_id=normalised.event_id,
        )
    except handlers.OrderChangeError as exc:
        if exc.status == 409:
            await _record_outcome("blocked", "event_already_recorded")
            return {"recorded": False, "reason": "event_already_recorded", "event_id": normalised.event_id}
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    kick_dispatch(tenant)
    capabilities = {"dev.ucp.shopping.order": [{"version": version}]}
    return {
        "recorded": True,
        "event_id": normalised.event_id,
        "revision": revision,
        "order": render_order(snapshot=updated, version=version, capabilities=capabilities),
    }


async def _carrier_aliases(session: Any, tenant: uuid.UUID, order: Any) -> dict[str, str]:
    from core.commerce.oacp_merchant_config import merchant_config_id
    from core.models.commerce_c6z_runtime import C6ZMerchantCommerceConfigRow

    config = await session.get(
        C6ZMerchantCommerceConfigRow, merchant_config_id(str(tenant), order.merchant_id, order.seller_agent_id)
    )
    ucp = ((getattr(config, "provider_policy", None) or {}).get("ucp") or {}) if config is not None else {}
    aliases = ucp.get("carrier_aliases") if isinstance(ucp, dict) else None
    return {str(k): str(v) for k, v in aliases.items()} if isinstance(aliases, dict) else {}


@runtime_router.post("/plural/events")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.ucp.orders.write",
    rate_limit="commerce-runtime-write",
    idempotency="plural-webhook-id",
    audit_event="commerce.ucp.plural.event",
)
async def relay_plural_event(request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """A Plural webhook the merchant's integration relays: verified with the Plural signature, then applied.

    The tenant comes from the authenticated caller; the event proves itself with
    the ``webhook-id``/``webhook-timestamp``/``webhook-signature`` headers Plural
    sent (HMAC with ``PLURAL_PINE_WEBHOOK_SECRET``), so the relay cannot forge
    one. A verified UPI AutoPay mandate outcome records the RBI AFA evidence; a
    verified order outcome reconciles a pending payment or a capture after
    3-D Secure into the payment journal and the checkout (idempotent).
    """
    from core.commerce.executors.plural import PluralWebhookError, apply_plural_event

    raw_body = await request.body()
    if len(raw_body) > 256 * 1024:
        raise HTTPException(status_code=413, detail="event too large")
    names = ("webhook-id", "webhook-timestamp", "webhook-signature")
    headers = {name: request.headers.get(name, "") for name in names}
    tenant = str(_tenant_uuid(tenant_id))
    try:
        result = await apply_plural_event(tenant_id=tenant, raw_body=raw_body, headers=headers)
    except PluralWebhookError as exc:
        await record_commerce_event_in_own_transaction(
            tenant_id=tenant,
            event="commerce.ucp.plural.event",
            outcome="refused",
            merchant_id=None,
            seller_agent_id=None,
            buyer_agent_id=None,
            payload={"body_sha256": hashlib.sha256(raw_body).hexdigest(), "reason": exc.code},
            details={"reason": exc.code},
        )
        raise HTTPException(status_code=400, detail=exc.code) from exc
    await record_commerce_event_in_own_transaction(
        tenant_id=tenant,
        event="commerce.ucp.plural.event",
        outcome="success",
        merchant_id=None,
        seller_agent_id=None,
        buyer_agent_id=None,
        payload={"body_sha256": result["body_sha256"], "event_type": result["event_type"]},
        details={
            "applied": result["applied"],
            "afa": result.get("afa"),
            "payment": result.get("payment"),
            "event_type": result["event_type"],
        },
    )
    return {"verified": True, "applied": result["applied"], "afa": result.get("afa"), "payment": result.get("payment")}
