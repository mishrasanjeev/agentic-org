# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The AgenticOrg shopping network ("Sell everywhere"): directory, checkout gateway, merchant opt-in.

Public, under ``/api/v1/network`` (no session; every route answers 404 while
``AGENTICORG_NETWORK_ENABLED`` is off):

* ``GET /network/merchants`` - the listed shops that are servable right now: slug,
  name, categories, the UCP profile URL and the Telegram deep link.
* ``POST /network/shops/{slug}/checkout-sessions``, ``GET``/``PUT
  /network/shops/{slug}/checkout-sessions/{id}`` - open, read and update a
  checkout at a listed shop for the AgenticOrg network platform. Only the
  network service credential (``AGENTICORG_NETWORK_SERVICE_TOKEN``, held by the
  hosted MCP server) is accepted. The UCP handlers run in the shop's tenant on
  its published host, exactly as a platform call to ``https://<host>/ucp``
  would; network checkouts answer to the network platform only. There is no
  complete, cancel or payment route: the buyer pays with the shop.

* ``POST /network/telegram/webhook`` - the public AgenticOrg shopping bot
  (``core.commerce.network_telegram``): Telegram's secret header is checked and the update is
  queued; 404 while ``AGENTICORG_NETWORK_TELEGRAM_ENABLED`` is off.

Merchant (``commerce.merchant_config.write`` or tenant admin), under
``/api/v1/commerce/runtime/merchant-configs/{merchant_id}/sell-everywhere``:
``GET`` where the shop stands on each surface; ``PUT`` turn the network on
(accepting the network terms) or pause it. A shop with no published host of
its own gets a platform subdomain ``<slug>.<apex>`` (``core.commerce.network``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1.commerce_runtime import require_merchant_commerce_config_write
from api.v1.commerce_ucp_publishing import _actor, _locked_row
from core.commerce import network as net
from core.commerce.ledger import record_commerce_event_in_own_transaction
from core.config import settings
from core.database import get_tenant_session
from core.models.merchant_custom_domain import INACTIVE_STATUSES, MerchantCustomDomainRow

logger = structlog.get_logger()

router = APIRouter(prefix="/network", tags=["Network"])
merchant_router = APIRouter(
    prefix="/commerce/runtime",
    tags=["Network"],
    dependencies=[Depends(require_merchant_commerce_config_write)],
)

GATEWAY_OPERATIONS = ("create_checkout", "get_checkout", "update_checkout")
MAX_GATEWAY_BODY_BYTES = 64 * 1024


def _require_network() -> None:
    if not net.network_enabled():
        raise HTTPException(404, "Not Found")


def _refusal(exc: net.NetworkError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": exc.message})


def _service_token_ok(request: Request) -> bool:
    expected = (settings.network_service_token or "").strip()
    if len(expected) < 32:
        return False
    presented = request.headers.get("authorization", "")
    if not presented.startswith("Bearer "):
        return False
    return hmac.compare_digest(
        hashlib.sha256(presented[7:].strip().encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )


# --- public directory -------------------------------------------------------------------------


@router.get("/merchants")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:network.directory.read",
    rate_limit="public-read",
    idempotency="read-only",
    audit_event="network.directory.read",
    public_reason="network-directory-lists-only-published-opted-in-shops",
)
async def list_network_merchants() -> JSONResponse:
    _require_network()
    shops = await net.directory()
    return JSONResponse({"shops": shops, "count": len(shops)}, headers={"Cache-Control": "public, max-age=60"})


# --- checkout gateway (network service credential) ----------------------------------------------


async def _gateway(operation: str, slug: str, request: Request, session_id: str | None = None) -> JSONResponse:
    _require_network()
    if not settings.ucp_checkout_enabled:
        raise HTTPException(404, "Not Found")
    if not _service_token_ok(request):
        return JSONResponse(
            {"code": "unauthorized", "message": "The network service credential is required."},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="agenticorg-network"'},
        )
    raw = await request.body()
    if len(raw) > MAX_GATEWAY_BODY_BYTES:
        raise HTTPException(413, "Request body too large")
    try:
        shop = await net.resolve_shop(slug)
    except net.NetworkError as exc:
        raise _refusal(exc) from None
    request_id = request.headers.get("request-id")
    response = await _run_for_network(
        operation,
        shop,
        raw,
        idempotency_key=request.headers.get("idempotency-key"),
        request_id=request_id,
        session_id=session_id,
    )
    headers = dict(response.headers)
    if request_id:
        headers["Request-Id"] = request_id[:128]
    return JSONResponse(status_code=response.status, content=response.body, headers=headers)


async def _run_for_network(
    operation: str,
    shop: net.ShopRef,
    raw: bytes,
    *,
    idempotency_key: str | None,
    request_id: str | None,
    session_id: str | None,
) -> Any:
    from api.v1.commerce_ucp import run_ucp_operation  # noqa: PLC0415 - commerce_ucp wires the UCP runtime at import

    if operation not in GATEWAY_OPERATIONS:
        raise ValueError(f"not a network operation: {operation}")
    profile = settings.network_agent_profile
    response = await run_ucp_operation(
        operation,
        tenant_id=shop.tenant_id,
        host=shop.host,
        ucp_agent=f'profile="{profile}"',
        raw_body=raw,
        idempotency_key=idempotency_key,
        request_id=request_id,
        resource_id=session_id,
        platform=profile,
    )
    logger.info("network_checkout_call", operation=operation, slug=shop.slug, status=response.status)
    return response


async def network_checkout(
    slug: str, operation: str, body: dict[str, Any] | None, session_id: str | None = None
) -> dict[str, Any]:
    """The network checkout path for in-process callers (the public Telegram bot).

    Same resolution and UCP handling as the gateway routes; returns the shop's answer with its
    HTTP status and the shop slug, or ``{"error": ...}`` when the shop is not available.
    """
    if not (net.network_enabled() and settings.ucp_checkout_enabled):
        return {"error": "checkout_unavailable"}
    try:
        shop = await net.resolve_shop(slug)
    except net.NetworkError as exc:
        return {"error": exc.code, "message": exc.message}
    raw = json.dumps(body).encode() if body is not None else b""
    response = await _run_for_network(
        operation,
        shop,
        raw,
        idempotency_key=str(uuid.uuid4()) if operation != "get_checkout" else None,
        request_id=str(uuid.uuid4()),
        session_id=session_id,
    )
    answer = response.body if isinstance(response.body, dict) else {"body": response.body}
    return {"http_status": response.status, "shop": shop.slug, **answer}


# --- public Telegram bot ----------------------------------------------------------------------


def _telegram_secret_ok(request: Request) -> bool:
    expected = (settings.network_telegram_webhook_secret or "").strip()
    presented = request.headers.get("x-telegram-bot-api-secret-token", "")
    if len(expected) < 24 or not presented:
        return False
    return hmac.compare_digest(hashlib.sha256(presented.encode()).digest(), hashlib.sha256(expected.encode()).digest())


@router.post("/telegram/webhook")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:network.telegram.webhook",
    rate_limit="provider-webhook",
    idempotency="telegram-update-id-claimed-once",
    audit_event="network.telegram.update",
    public_reason="telegram-webhook-secret-checked-in-handler",
)
async def network_telegram_webhook(request: Request) -> JSONResponse:
    if not (net.network_enabled() and settings.network_telegram_enabled):
        raise HTTPException(404, "Not Found")
    if not _telegram_secret_ok(request):
        raise HTTPException(403, "Forbidden")
    raw = await request.body()
    if len(raw) > MAX_GATEWAY_BODY_BYTES * 4:
        return JSONResponse({"ok": True})
    try:
        update = json.loads(raw)
    except ValueError:
        return JSONResponse({"ok": True})
    from core.tasks.network_tasks import handle_telegram_update  # noqa: PLC0415 - Celery app import on demand

    handle_telegram_update.delay(update)
    return JSONResponse({"ok": True})


@router.post("/shops/{slug}/checkout-sessions", status_code=201)
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="network.checkout.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="network.checkout.create",
    public_reason="network-service-credential-checked-in-handler",
)
async def network_create_checkout(slug: str, request: Request) -> JSONResponse:
    return await _gateway("create_checkout", slug, request)


@router.get("/shops/{slug}/checkout-sessions/{session_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="network.checkout.read",
    rate_limit="ucp-platform",
    idempotency="read-only",
    audit_event="network.checkout.read",
    public_reason="network-service-credential-checked-in-handler",
)
async def network_get_checkout(slug: str, session_id: str, request: Request) -> JSONResponse:
    return await _gateway("get_checkout", slug, request, session_id)


@router.put("/shops/{slug}/checkout-sessions/{session_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="network.checkout.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="network.checkout.update",
    public_reason="network-service-credential-checked-in-handler",
)
async def network_update_checkout(slug: str, session_id: str, request: Request) -> JSONResponse:
    return await _gateway("update_checkout", slug, request, session_id)


# --- merchant: Sell everywhere ----------------------------------------------------------------


class SellEverywhereRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listed: bool
    accept_terms: bool = False
    slug: str | None = Field(default=None, min_length=3, max_length=40)
    seller_agent_id: str | None = Field(default=None, min_length=1, max_length=160)
    claim_subdomain: bool = True


async def _view(tenant_id: str, merchant_id: str, seller_agent_id: str | None) -> dict[str, Any]:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        config = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id, lock=False
        )
        seller = config.seller_agent_id or ""
        listing = await net.get_listing(
            session, tenant_id=tenant_id, merchant_id=config.merchant_id, seller_agent_id=seller
        )
        domain = (
            await session.scalars(
                select(MerchantCustomDomainRow).where(
                    MerchantCustomDomainRow.tenant_id == uuid.UUID(tenant_id),
                    MerchantCustomDomainRow.merchant_id == config.merchant_id,
                    MerchantCustomDomainRow.seller_agent_id == seller,
                    MerchantCustomDomainRow.status.not_in(INACTIVE_STATUSES),
                )
            )
        ).first()
    in_use = await net.platform_subdomains_in_use() if net.platform_apex() else 0
    return {
        "enabled": True,
        "terms_version": settings.network_terms_version,
        "suggested_slug": net.slugify(config.merchant_display_name or config.merchant_id),
        "listing": net.listing_view(listing),
        "surfaces": net.surfaces_view(listing, config, domain),
        "platform_subdomains": {
            "apex": net.platform_apex() or None,
            "in_use": in_use,
            "cap": int(settings.platform_subdomain_cap),
        },
    }


@merchant_router.get("/merchant-configs/{merchant_id}/sell-everywhere")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="network.sell_everywhere.read",
)
async def get_sell_everywhere(
    merchant_id: str,
    seller_agent_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    _require_network()
    return await _view(tenant_id, merchant_id, seller_agent_id)


@merchant_router.put("/merchant-configs/{merchant_id}/sell-everywhere")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="listing-state-converges",
    audit_event="commerce.network.sell_everywhere.write",
)
async def put_sell_everywhere(
    merchant_id: str,
    body: SellEverywhereRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    _require_network()
    if body.listed and not body.accept_terms:
        raise HTTPException(
            422, {"code": "terms_not_accepted", "message": "Accept the network terms to list the shop."}
        )
    actor = _actor(request)
    actor_label = f"{actor.get('actor_type', 'user')}:{actor.get('actor_id', 'unknown')}"
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        config = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id, lock=False
        )
    try:
        listing = await net.save_listing(
            tenant_id=tenant_id, config=config, listed=body.listed, slug=body.slug, actor_label=actor_label
        )
        subdomain_note = None
        if body.listed and body.claim_subdomain and net.published_host(config) is None:
            try:
                await net.claim_platform_subdomain(
                    tenant_id=tenant_id, config=config, listing=listing, actor=actor, actor_label=actor_label
                )
            except net.NetworkError as exc:
                # The listing stands; the page shows why no platform address was given.
                subdomain_note = {"code": exc.code, "message": exc.message}
    except net.NetworkError as exc:
        await record_commerce_event_in_own_transaction(
            tenant_id=tenant_id,
            event="commerce.network.sell_everywhere.write",
            outcome="refused",
            merchant_id=config.merchant_id,
            seller_agent_id=config.seller_agent_id or "",
            buyer_agent_id=None,
            payload={"listed": body.listed, "reason": exc.code},
            resource_id=config.config_id,
        )
        raise _refusal(exc) from None
    view = await _view(tenant_id, merchant_id, body.seller_agent_id)
    view["subdomain_note"] = subdomain_note
    return view
